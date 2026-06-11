@echo off
set "is_build="
for %%a in (%*) do (
    if "%%a"=="--build" set "is_build=1"
)

if defined is_build (
    cmake.exe %*
) else (
    cmake.exe -DCMAKE_POLICY_VERSION_MINIMUM=3.5 %*
)
