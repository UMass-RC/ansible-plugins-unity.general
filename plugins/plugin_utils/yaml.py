import yaml
from ansible.parsing.yaml.dumper import AnsibleDumper


# from http://stackoverflow.com/a/15423007/115478
def _should_use_block(value):
    """Returns true if string should be in block format"""
    for c in "\u000a\u000d\u001c\u001d\u001e\u0085\u2028\u2029":
        if c in value:
            return True
    return False


# stolen from community.general.yaml callback plugin
class HumanReadableYamlDumper(AnsibleDumper):
    def represent_scalar(self, tag, value, style=None):
        """Uses block style for multi-line strings"""
        if style is None:
            if _should_use_block(value):
                style = "|"
            else:
                style = self.default_style
        node = yaml.representer.ScalarNode(tag, value, style=style)
        if self.alias_key is not None:
            self.represented_objects[self.alias_key] = node
        return node


def yaml_dump(x):
    return yaml.dump(
        x,
        allow_unicode=True,
        width=-1,  # FIXME -1 works only for CSafeDumper, if libyaml not present this won't work
        Dumper=HumanReadableYamlDumper,
        default_flow_style=False,
    )
