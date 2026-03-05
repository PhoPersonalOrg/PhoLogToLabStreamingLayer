# PyOxidizer Configuration File
# Builds single, self-contained executables for Windows, macOS, and Linux
# Documentation: https://pyoxidizer.readthedocs.io/en/stable/pyoxidizer_config_file.html

def make_exe():
    dist = default_python_distribution()
    
    # Configure the packaging policy
    policy = dist.make_python_packaging_policy()
    
    # We want to extract extensions to the filesystem as `mne`, `torch`, `lsl` etc. might contain compiled code
    # that fails to load from memory on Windows/macOS.
    policy.set_resource_handling_mode("classify")
    policy.resources_location = "in-memory"
    policy.resources_location_fallback = "filesystem-relative:lib"
    policy.extension_module_filter = "all"
    policy.include_test = False
    
    python_config = dist.make_python_interpreter_config()
    
    # Because our root entry point script `logger_app.py` defines `console_main`, we can run it like a module if it's packed, 
    # but we can better execute the entry point configured in pyproject.toml
    # Let's run `logger_app.console_main()`
    python_config.run_command = "from logger_app import console_main; console_main()"
    # Alternatively we can use run_module = "logger_app", but logger_app executes console_main() directly only if __name__ == "__main__"
    
    exe = dist.to_python_executable(
        name="logger_app",
        packaging_policy=policy,
        config=python_config,
    )
    
    # Local path deps must be listed explicitly because PyOxidizer's pip does not
    # read [tool.uv.sources]. Install them first (in dependency order) so that when
    # pip installs "." it finds them already satisfied.
    exe.add_python_resources(exe.pip_install(["../PhoPyLSLhelper", "../lab-recorder-python", "../whisper-timestamped", "."]))
    
    return exe

def make_install(exe):
    # The Install target represents a directory where the executable and required filesystem resources are placed.
    files = FileManifest()
    files.add_python_resource(".", exe)
    return files

register_target("exe", make_exe)
register_target("install", make_install, depends=["exe"], default=True)
resolve_targets()
