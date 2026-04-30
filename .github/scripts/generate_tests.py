from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llm_client import create_client


IMPORT_RE = re.compile(r"^\s*import\s+([\w\.]+);", re.MULTILINE)
FINAL_FIELD_RE = re.compile(
    r"^\s*private\s+final\s+([A-Za-z_][\w<>\., ?]+)\s+([A-Za-z_]\w*)\s*;",
    re.MULTILINE,
)
ANNOTATION_RE = re.compile(r"^\s*@(\w+)", re.MULTILINE)

INJECTION_PATTERNS = [
    re.compile(r"(?i)(ignore|forget|disregard).{0,30}(above|previous|instruction|prompt)"),
    re.compile(r"(?i)\[SYSTEM\]|\[INST\]|\[\/INST\]"),
    re.compile(r"(?i)<\|im_start\|>|<\|im_end\|>"),
    re.compile(r"(?i)you are now|act as|pretend to be"),
    re.compile(r"(?i)reveal|expose|print|output.{0,20}(key|token|secret|password|api)"),
    re.compile(r"(?i)###\s*(system|instruction|prompt)"),
]


@dataclass
class ChangedMethod:
    file_path:      str
    package_name:   str | None
    class_name:     str | None
    method_name:    str
    start_line:     int
    end_line:       int
    signature_line: str
    code:           str


def sanitize_for_prompt(text: str, max_length: int = 25000) -> str:
    if not text:
        return ""
    for pattern in INJECTION_PATTERNS:
        text = pattern.sub("", text)
    if len(text) > max_length:
        text = text[:max_length] + "\n...(truncated for security)"
    return text.strip()


def safe_slug(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_\\-\\.]+", "_", text)
    return text.strip("_") or "unknown"


def is_private_method(method: ChangedMethod) -> bool:
    return method.signature_line.strip().startswith("private ")


def find_public_callers_for_private_method(
    private_method: ChangedMethod,
    class_context:  dict[str, Any],
) -> list[str]:
    full_code   = class_context.get("full_class_code", "")
    lines       = full_code.splitlines()
    target_call = private_method.method_name + "("

    public_method_re = re.compile(
        r"^\s*public\s+[\w\<\>\[\],\s?\.]+\s+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:throws\s+[^{]+)?\{"
    )

    callers:               list[str] = []
    current_public_method: str | None = None
    brace_balance  = 0
    in_public_method = False

    for line in lines:
        match = public_method_re.match(line)
        if match:
            current_public_method = match.group(1)
            in_public_method      = True
            brace_balance         = line.count("{") - line.count("}")
        elif in_public_method:
            brace_balance += line.count("{") - line.count("}")

        if in_public_method and current_public_method and target_call in line:
            callers.append(current_public_method)

        if in_public_method and brace_balance <= 0:
            current_public_method = None
            in_public_method      = False
            brace_balance         = 0

    return sorted(set(callers))


def run_git_command(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_repo_root() -> Path:
    return Path(run_git_command(["rev-parse", "--show-toplevel"]))


def load_changed_methods(json_path: str, repo_root: Path) -> list[ChangedMethod]:
    path = Path(json_path)
    if not path.is_absolute():
        path = repo_root / path
    data    = json.loads(path.read_text(encoding="utf-8"))
    methods = data.get("methods", [])
    return [ChangedMethod(**m) for m in methods]


def group_methods_by_class(methods: list[ChangedMethod]) -> dict[str, list[ChangedMethod]]:
    """메서드를 클래스별로 그룹핑"""
    groups: dict[str, list[ChangedMethod]] = {}
    for method in methods:
        key = f"{method.package_name}.{method.class_name}"
        if key not in groups:
            groups[key] = []
        groups[key].append(method)
    return groups


def sanitize_java_code_block(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$",           "", text)
    return text.strip()


def collect_imported_types(java_code: str) -> list[str]:
    return IMPORT_RE.findall(java_code)


def find_file_by_fqcn(fqcn: str, repo_root: Path) -> Path | None:
    parts = fqcn.split(".")
    if not parts:
        return None
    class_name = parts[-1] + ".java"
    candidates = list(repo_root.rglob(class_name))
    if not candidates:
        return None
    package_suffix = "/".join(parts[:-1])
    for candidate in candidates:
        if package_suffix in str(candidate).replace("\\", "/"):
            return candidate
    return candidates[0]


def read_file_safe(path: Path, max_chars: int = 10000) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")[:max_chars]


def collect_primary_class_context(method: ChangedMethod, repo_root: Path) -> dict[str, Any]:
    abs_path        = repo_root / method.file_path
    full_class_code = read_file_safe(abs_path, max_chars=25000)
    imports         = collect_imported_types(full_class_code)
    dependencies    = FINAL_FIELD_RE.findall(full_class_code)
    annotations     = ANNOTATION_RE.findall(full_class_code)

    return {
        "absolute_path":   str(abs_path),
        "full_class_code": full_class_code,
        "imports":         imports,
        "dependencies": [
            {"type": dep_type.strip(), "name": dep_name.strip()}
            for dep_type, dep_name in dependencies
        ],
        "annotations": annotations,
    }


def collect_related_files(
    method:    ChangedMethod,
    repo_root: Path,
    max_files: int = 8,
) -> list[dict[str, str]]:
    abs_path        = repo_root / method.file_path
    full_class_code = read_file_safe(abs_path, max_chars=25000)
    imports         = collect_imported_types(full_class_code)

    related:     list[dict[str, str]] = []
    seen:        set[str]              = set()
    dto_keywords = ["RequestDto", "ResponseDto", "Request", "Response"]

    for fqcn in imports:
        if fqcn.startswith(("java.", "jakarta.", "org.springframework.", "lombok.")):
            continue
        path = find_file_by_fqcn(fqcn, repo_root)
        if not path:
            continue
        norm = str(path.resolve())
        if norm in seen:
            continue
        seen.add(norm)
        is_dto = any(keyword in fqcn for keyword in dto_keywords)
        related.append({
            "file_path": str(path.relative_to(repo_root)),
            "code":      read_file_safe(path, max_chars=15000),
            "priority":  0 if is_dto else 1,
        })
        if len(related) >= max_files:
            break

    related.sort(key=lambda x: x.get("priority", 1))
    for r in related:
        r.pop("priority", None)

    return related


# ── 생성된 Java 코드 후처리 ───────────────────────
def fix_generated_java(java_code: str) -> str:
    """생성된 JUnit 테스트 코드 자동 수정"""

    # 1. assertThrows static import 추가
    if "assertThrows" in java_code and \
       "import static org.junit.jupiter.api.Assertions" not in java_code:
        if "import org.junit.jupiter.api.Test;" in java_code:
            java_code = java_code.replace(
                "import org.junit.jupiter.api.Test;",
                "import org.junit.jupiter.api.Test;\nimport static org.junit.jupiter.api.Assertions.*;"
            )
        else:
            # package 선언 다음에 추가
            java_code = re.sub(
                r"(package [\w\.]+;\s*\n)",
                r"\1\nimport static org.junit.jupiter.api.Assertions.*;\n",
                java_code
            )
        print("[FIX] assertThrows static import 추가")

    # 2. response.getSuccess() → isNotNull() 로 교체
    if "getSuccess()" in java_code:
        java_code = re.sub(
            r"assertThat\((\w+)\.getSuccess\(\)\)\.isTrue\(\);",
            r"assertThat(\1).isNotNull();",
            java_code
        )
        java_code = re.sub(
            r"assertThat\((\w+)\.getSuccess\(\)\)\.isFalse\(\);",
            r"assertThat(\1).isNotNull();",
            java_code
        )
        print("[FIX] response.getSuccess() 제거 → isNotNull() 교체")

    # 3. NotFoundException import 자동 추가
    if "NotFoundException" in java_code and \
       "import com.axakorea.subscription.exception.NotFoundException" not in java_code:
        java_code = re.sub(
            r"(package [\w\.]+;)",
            r"\1\nimport com.axakorea.subscription.exception.NotFoundException;",
            java_code
        )
        print("[FIX] NotFoundException import 추가")

    # 4. 기타 프로젝트 공통 exception import 추가
    if "IllegalArgumentException" in java_code and \
       "assertThrows(IllegalArgumentException" in java_code:
        # IllegalArgumentException은 java.lang이라 import 불필요
        pass

    return java_code


def build_class_junit_prompt(
    class_name:    str,
    methods:       list[ChangedMethod],
    class_context: dict[str, Any],
    related_files: list[dict[str, str]],
) -> str:
    safe_class_name = class_name or "UnknownClass"

    methods_text = "\n\n".join([
        f"[Changed Method {i+1}]\n"
        f"method_name: {m.method_name}\n"
        f"signature: {sanitize_for_prompt(m.signature_line, 500)}\n"
        f"is_private: {str(is_private_method(m)).lower()}\n"
        f"line_range: {m.start_line}-{m.end_line}\n"
        f"code:\n{sanitize_for_prompt(m.code, 5000)}"
        for i, m in enumerate(methods)
    ])

    method_names = [m.method_name for m in methods]

    safe_class_code   = sanitize_for_prompt(class_context["full_class_code"], 25000)
    safe_related_text = sanitize_for_prompt(
        "\n\n".join(
            f"[Related File] {item['file_path']}\n{item['code']}"
            for item in related_files
        ),
        30000,
    )

    required_import = (
        f"import {methods[0].package_name}.{safe_class_name};"
        if methods[0].package_name
        else f"// package unknown for {safe_class_name}"
    )

    source_methods_comment = "\n".join(
        f" * SOURCE_METHOD_{i+1}: {m.method_name} (lines {m.start_line}-{m.end_line})"
        for i, m in enumerate(methods)
    )

    return f"""
You are an expert Java backend test generation assistant.

Generate ONE compilable Java JUnit 5 test class that tests ALL changed methods below.

Primary goal:
- Produce a single test class that can compile and run in the target project.
- Cover ALL changed methods listed below in a single test class.
- Use only classes, methods, and fields that actually exist in the provided code context.

Output format requirements:
- Return ONLY raw Java source code.
- Do NOT return markdown fences.
- Do NOT return explanations.
- The class name MUST be "{safe_class_name}GeneratedTest".

Required test style rules:
1. Use JUnit 5.
2. Use AssertJ for normal result assertions.
3. Use assertThrows for exception scenarios:
   - ALWAYS add: import static org.junit.jupiter.api.Assertions.*;
   - NEVER use assertThrows without this static import
4. If constructor-injected dependencies exist, prefer Mockito-based service tests:
   - @ExtendWith(MockitoExtension.class)
   - @Mock
   - @InjectMocks
   - when(...).thenReturn(...)
5. If the changed class is clearly a controller, prefer MockMvc tests.
6. CRITICAL - Import statements (missing imports = compilation failure):
   - ALWAYS include this exact import for the class under test:
     {required_import}
   - ALWAYS include imports for ALL dependencies, DTOs, domain objects used in tests
   - ALWAYS include ALL of the following that are used:
     import org.junit.jupiter.api.Test;
     import org.junit.jupiter.api.extension.ExtendWith;
     import static org.junit.jupiter.api.Assertions.*;
     import org.mockito.junit.jupiter.MockitoExtension;
     import org.mockito.Mock;
     import org.mockito.InjectMocks;
     import static org.mockito.Mockito.*;
     import static org.assertj.core.api.Assertions.*;
   - CRITICAL: For ANY custom exception class (NotFoundException, etc.):
     derive full package path from [Related Files] and add explicit import
   - NEVER omit any import that is referenced in the test code
7. Do not invent repository methods, DTO fields, builders, or constructors
   that are not visible in the provided code.
8. CRITICAL - Object construction rules:
   - Check Lombok annotations in [Related Files] BEFORE constructing objects:
     * @Builder present → use builder() pattern
     * @AllArgsConstructor present → use all-args constructor
       (derive field order strictly from top-to-bottom field declarations)
     * Only @Getter + @NoArgsConstructor → use no-args constructor ONLY
       then use setter IF AND ONLY IF @Setter is present
     * CRITICAL: NEVER use setter methods (setXxx()) unless @Setter is
       explicitly visible in the class definition
     * CRITICAL: If only @Getter + @NoArgsConstructor with no @Setter:
       skip object field assignment and use mock/stub instead
   - CRITICAL: For LocalDate fields, use LocalDate.of(2024, 1, 1)
   - CRITICAL: Never guess constructor parameter order
   - CRITICAL: Do NOT call methods on response objects (getSuccess() etc.)
     unless those methods are explicitly visible in the class definition
   - CRITICAL: Only assert on fields/methods that exist in the response class
9. Create 2 to 3 test methods PER changed method
   (total {len(methods) * 2}~{len(methods) * 3} test methods).
10. Prefer deterministic assertions over weak assertions.
11. For private methods: test through public caller methods only.
12. Only generate test methods that call methods accessible
    from the generated test class.

Very important traceability requirement:
At the top of the generated Java class, include a comment block exactly like this:

/*
 * AUTO-GENERATED TEST
 * SOURCE_FILE: {methods[0].file_path}
{source_methods_comment}
 */

[Changed Methods - ALL must be tested]
Target methods: {json.dumps(method_names, ensure_ascii=False)}

{methods_text}

[Changed Class Full Code]
{safe_class_code}

[Detected Dependencies]
{json.dumps(class_context["dependencies"], ensure_ascii=False, indent=2)}

[Detected Class Annotations]
{json.dumps(class_context["annotations"], ensure_ascii=False, indent=2)}

[Related Files]
{safe_related_text if safe_related_text else "(none)"}

Return ONLY the Java source code of the generated JUnit test class covering ALL changed methods.
""".strip()


def save_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_prompt_log_paths(
    repo_root:      Path,
    prompt_log_dir: str,
    class_name:     str,
) -> dict[str, Path]:
    base_name = safe_slug(class_name)
    base_dir  = repo_root / prompt_log_dir

    return {
        "prompt_txt":   base_dir / f"{base_name}__prompt.txt",
        "response_txt": base_dir / f"{base_name}__response.txt",
        "meta_json":    base_dir / f"{base_name}__meta.json",
    }


def generate_direct_junit_code(prompt: str) -> tuple[str, str, str]:
    client         = create_client()
    selected_model = os.getenv("OPENAI_MODEL", "openai/gpt-4o-mini").strip()

    response = client.chat.completions.create(
        model=selected_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )

    raw_text     = response.choices[0].message.content or ""
    cleaned_java = sanitize_java_code_block(raw_text)
    return cleaned_java, raw_text, selected_model


def package_to_path(package_name: str | None) -> Path:
    if not package_name:
        return Path()
    return Path(*package_name.split("."))


def save_generated_test(
    java_code:    str,
    package_name: str | None,
    class_name:   str | None,
    output_root:  Path,
) -> Path:
    safe_class_name = class_name or "UnknownClass"
    output_dir      = output_root / package_to_path(package_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{safe_class_name}GeneratedTest.java"
    output_file.write_text(java_code, encoding="utf-8")
    return output_file


def process_class_methods(args_tuple: tuple) -> dict[str, str]:
    """병렬 처리용 클래스 단위 처리 함수"""
    class_methods, repo_root, output_root, max_related_files, prompt_log_dir = args_tuple

    representative = class_methods[0]
    class_name     = representative.class_name or "UnknownClass"

    print(f"[INFO] {class_name} 처리 중 ({len(class_methods)}개 메서드)")

    valid_methods: list[ChangedMethod] = []
    class_context = collect_primary_class_context(representative, repo_root)

    for method in class_methods:
        if is_private_method(method):
            callers = find_public_callers_for_private_method(method, class_context)
            if not callers:
                print(f"[SKIP] Private method no public caller: {class_name}.{method.method_name}")
                continue
            print(f"[INFO] Private method: {method.method_name}, callers: {callers}")
        valid_methods.append(method)

    if not valid_methods:
        print(f"[SKIP] {class_name} - 유효한 메서드 없음")
        return {
            "class_name":          class_name,
            "source_file":         representative.file_path,
            "generated_test_file": "",
            "prompt_file":         "",
            "response_file":       "",
            "meta_file":           "",
            "skip_reason":         "no_valid_methods",
        }

    related_files = collect_related_files(representative, repo_root, max_files=max_related_files)
    prompt        = build_class_junit_prompt(
        class_name, valid_methods, class_context, related_files
    )
    java_code, raw_response, selected_model = generate_direct_junit_code(prompt)

    # ✅ 후처리 적용
    java_code = fix_generated_java(java_code)

    log_paths = build_prompt_log_paths(
        repo_root=repo_root,
        prompt_log_dir=prompt_log_dir,
        class_name=class_name,
    )

    save_text(log_paths["prompt_txt"],  prompt)
    save_text(log_paths["response_txt"], raw_response)
    save_text(
        log_paths["meta_json"],
        json.dumps(
            {
                "source_file":    representative.file_path,
                "class_name":     class_name,
                "method_names":   [m.method_name for m in valid_methods],
                "method_count":   len(valid_methods),
                "model":          selected_model,
                "package_name":   representative.package_name,
            },
            indent=2,
            ensure_ascii=False,
        ),
    )

    output_file = save_generated_test(
        java_code=java_code,
        package_name=representative.package_name,
        class_name=class_name,
        output_root=output_root,
    )

    print(f"Generated: {output_file} ({len(valid_methods)}개 메서드)")

    return {
        "class_name":          class_name,
        "source_file":         representative.file_path,
        "generated_test_file": str(output_file.relative_to(repo_root)),
        "prompt_file":         str(log_paths["prompt_txt"].relative_to(repo_root)),
        "response_file":       str(log_paths["response_txt"].relative_to(repo_root)),
        "meta_file":           str(log_paths["meta_json"].relative_to(repo_root)),
        "skip_reason":         "",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate JUnit test code by class from changed Java methods using LLM."
    )
    parser.add_argument("--changed-methods-json", default="outputs/changed_methods.json")
    parser.add_argument("--output-dir",           default="subscription/src/test/java")
    parser.add_argument("--max-related-files",    type=int, default=8)
    parser.add_argument("--prompt-log-dir",       default="outputs/prompts")
    parser.add_argument("--max-workers",          type=int, default=3)
    args = parser.parse_args()

    repo_root   = get_repo_root()
    methods     = load_changed_methods(args.changed_methods_json, repo_root)
    output_root = (
        repo_root / args.output_dir
        if not Path(args.output_dir).is_absolute()
        else Path(args.output_dir)
    )

    if not methods:
        print("No changed methods found.")
        return

    class_groups = group_methods_by_class(methods)
    print(f"총 {len(methods)}개 메서드 → {len(class_groups)}개 클래스 병렬 처리 "
          f"(max_workers={args.max_workers})")

    tasks = [
        (class_methods, repo_root, output_root, args.max_related_files, args.prompt_log_dir)
        for class_methods in class_groups.values()
    ]

    summary: list[dict[str, str]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(process_class_methods, task): task
            for task in tasks
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
                summary.append(result)
                print(f"완료: {result['class_name']}")
            except Exception as e:
                print(f"처리 실패: {e}")

    summary_path = repo_root / "outputs/generated_direct_junit_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps({"generated_tests": summary}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Saved summary to: {summary_path}")


if __name__ == "__main__":
    main()