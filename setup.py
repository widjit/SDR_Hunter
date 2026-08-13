"""Installable package definition for SDR Hunter."""
from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()


def _read_requirements(path: str):
    reqs = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                reqs.append(line)
    return reqs


setup(
    name="sdr-hunter",
    version="1.0.0",
    description="Multi-SDR signal hunting, drone detection and analysis suite",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="SDR Hunter",
    python_requires=">=3.9",
    packages=find_packages(exclude=("tests",)),
    include_package_data=True,
    package_data={
        "config": ["*.json"],
        "database": ["*.sql"],
        "web": ["static/*"],
    },
    install_requires=_read_requirements("requirements.txt"),
    extras_require={"full": _read_requirements("requirements_optional.txt")},
    entry_points={
        "console_scripts": [
            "sdr-hunter=main:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Topic :: Communications :: Ham Radio",
    ],
)
