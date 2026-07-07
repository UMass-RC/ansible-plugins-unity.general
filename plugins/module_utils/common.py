import re
import jc

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


NVIDIA_PCI_VENDOR_ID = "10de:"


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
    model_name = model_name.replace("rev. a", "")
    model_name = model_name.replace("a2 / a16", "a16")
    model_name = model_name.replace("6000/8000", "8000")
    if "8000" not in model_name:
        model_name = model_name.replace("rtx", "")
    model_name = re.sub(r"\d+gb / \d+gb", "", model_name)
    model_name = re.sub(r"\d+gb", "", model_name)
    model_name = model_name.replace("pcie", "")
    model_name = re.sub(r"sxm\d+", "", model_name)
    model_name = model_name.strip("_- ")
    model_name = re.sub(r"\s+", "_", model_name)
    return model_name


def get_gpu_model_and_count(_module) -> tuple[str, int]:
    # `-q` means query the online PCI ID database, `-mmv` is required by `jc`
    stdout = _check_output(
        ["lspci", "-q", "-mmv", "-d", NVIDIA_PCI_VENDOR_ID], _module, timeout_sec=5
    )
    # raw=True because `physlot_int` is broken for some nodes (ex: gpu042)
    nvidia_pci_devices = jc.parse("lspci", stdout, raw=True)
    nvidia_gpus = [
        x
        for x in nvidia_pci_devices
        if x["class"] in ["3D controller", "VGA compatible controller"]
    ]
    # extract from square brackets - example: "GH100 [H200 NVL]"
    nvidia_gpu_models = [re.sub(r"^.*\[(.*)\].*", r"\1", x["device"]) for x in nvidia_gpus]
    assert all_elements_equal(nvidia_gpu_models)
    return translate_nvidia_gpu_model_name(nvidia_gpu_models[0]), len(nvidia_gpu_models)
