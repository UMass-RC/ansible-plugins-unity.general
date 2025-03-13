import re
import json
import yaml

# from ansible.parsing.yaml.dumper import AnsibleDumper


class PrettierYamlDumper(yaml.SafeDumper):
    """
    prettier-compliant yaml dumper
    for some reason, when using AnsibleDumper, the increase_indent trick will not work,
    even if I call yaml.SafeDumper.increase_indent directly. Without AnsibleDumper, yaml
    has issues with data types and is extremely hard to debug. To convert the types, I just
    dump to JSON and then load again. Not efficient, but it works.
    """

    def increase_indent(self, *args, **kwargs):
        "make sure list items are indented"
        return super().increase_indent(indentless=False, flow=False)

    def represent_scalar(self, tag, value, style=None):
        "make sure digit strings are in quotes"
        if re.fullmatch(r"tag:yaml\.org,\d+:str", tag) and value.isdigit():
            style = '"'
        return super().represent_scalar(tag, value, style)


def to_prettier_yaml(x: str, indent=2, sort_keys=False) -> str:
    x_munged = json.loads(json.dumps(x))
    return yaml.dump(x_munged, Dumper=PrettierYamlDumper, indent=indent, sort_keys=sort_keys)


class FilterModule(object):
    def filters(self):
        return {"to_prettier_yaml": to_prettier_yaml}
