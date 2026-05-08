# Handle circular import gracefully - if case_runner is still being imported,
# we'll import it lazily when needed
try:
    from .case_runner import run_cases
except (ImportError, AttributeError):
    # Module is still being imported, will be available later
    def run_cases(*args, **kwargs):
        from .case_runner import run_cases
        return run_cases(*args, **kwargs)

try:
    from .code_runner import run_code
except (ImportError, AttributeError):
    def run_code(*args, **kwargs):
        from .code_runner import run_code
        return run_code(*args, **kwargs)

try:
    from .input_generation import generate_inputs
except (ImportError, AttributeError):
    def generate_inputs(*args, **kwargs):
        from .input_generation import generate_inputs
        return generate_inputs(*args, **kwargs)

try:
    from .local_visualization import local_visualization
except (ImportError, AttributeError):
    def local_visualization(*args, **kwargs):
        from .local_visualization import local_visualization
        return local_visualization(*args, **kwargs)

__all__ = ["generate_inputs", "local_visualization", "run_cases", "run_code"]
