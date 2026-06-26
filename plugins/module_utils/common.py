import re

from ansible.module_utils.basic import AnsibleModule


def all_elements_equal(x: list) -> bool:
    if len(x) < 2:
        return True
    first_elem = x[0]
    for elem in x[1:]:
        if elem != first_elem:
            return False
    return True


def _check_output(argv: list[str], _module: AnsibleModule, timeout_sec=0) -> str:
    _, stdout, _ = _module.run_command(["timeout", "-v", str(timeout_sec)] + argv, check_rc=True)
    return stdout


def translate_nvidia_gpu_model_name(model_name: str) -> str:
    """
    model names follow no consistent naming scheme
    `lshw` names and `nvidia-smi` names are equally inconsistent
    here are the names that I know this works for:
        NVIDIA A100-PCIE-40GB
        NVIDIA A100 80GB PCIe
        NVIDIA A100-SXM4-80GB
        NVIDIA A40
        NVIDIA GeForce GTX 1080 Ti
        NVIDIA GeForce GTX TITAN X
        NVIDIA GeForce RTX 2080
        NVIDIA GeForce RTX 2080 Ti
        Quadro RTX 8000
        Tesla M40 24GB
        Tesla V100-PCIE-16GB
        Tesla V100-SXM2-16GB
        Tesla V100-SXM2-32GB
        NVIDIA H100 80GB HBM3
    """
    model_name = model_name.lower()
    model_name = model_name.replace("nvidia", "")
    model_name = model_name.replace("geforce", "")
    model_name = model_name.replace("quadro", "")
    model_name = model_name.replace("tesla", "")
    model_name = model_name.replace("gtx", "")
    model_name = model_name.replace("hbm3", "")
    if "8000" not in model_name:
        model_name = model_name.replace("rtx", "")
    model_name = re.sub(r"\d+gb", "", model_name)
    model_name = model_name.replace("pcie", "")
    model_name = re.sub(r"sxm\d+", "", model_name)
    model_name = model_name.strip("_- ")
    model_name = re.sub(r"\s+", "_", model_name)
    return model_name
