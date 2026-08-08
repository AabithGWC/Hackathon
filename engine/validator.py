"""
Generic JSON-schema validation with a single retry, reusable across all agent playbooks.
"""
from jsonschema import Draft7Validator


class ValidationFailedError(Exception):
    """Raised when LLM output fails schema validation after the retry attempt."""
    pass


def validate(data: dict, schema: dict) -> list:
    """Return a list of human-readable validation error strings (empty if valid)."""
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
    return [f"{'.'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors]


def validate_with_retry(call_fn, schema: dict, context: str = ""):
    """
    Call `call_fn()` (which must return a dict) and validate it against `schema`.
    On failure, call `call_fn()` a second time and validate again.
    Raises ValidationFailedError with a clear message if the second attempt also fails.
    """
    last_errors = []
    for attempt in (1, 2):
        try:
            data = call_fn()
        except Exception as exc:
            last_errors = [f"LLM call raised an exception: {exc}"]
            continue

        errors = validate(data, schema)
        if not errors:
            return data
        last_errors = errors

    context_msg = f" (context: {context})" if context else ""
    raise ValidationFailedError(
        f"LLM output failed schema validation after retry{context_msg}. "
        f"Errors: {'; '.join(last_errors)}"
    )
