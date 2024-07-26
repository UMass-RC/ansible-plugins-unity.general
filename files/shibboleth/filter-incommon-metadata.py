#!/usr/bin/env python3
"""
This script is used to download the latest metadata from InCommon and
filter it based on the various entities we use for Unity. It takes a JSON list of strings from
stdin, and prints XML to stdout.
"""

import sys
import json
import urllib.error
import urllib.request
from io import BytesIO
import xml.etree.ElementTree as ET  # filtering logic doesn't work with lxml

from lxml import etree as lxml_etree  # xmlsec requires lxml
import xmlsec

ENTITIES_DESCRIPTOR_ELEMENT = "<EntitiesDescriptor></EntitiesDescriptor>\n"
INCOMMON_METADATA_URL = "http://md.incommon.org/InCommon/InCommon-metadata.xml"
INCOMMON_METADATA_CERT_URL = "https://ds.incommon.org/certs/inc-md-cert.pem"


def verify_incommon_metadata(metadata_file: BytesIO) -> None:
    # https://xmlsec.readthedocs.io/en/stable/examples.html#verify
    template = lxml_etree.parse(metadata_file).getroot()
    xmlsec.tree.add_ids(template, ["ID"])
    signature_node = xmlsec.tree.find_node(template, xmlsec.constants.NodeSignature)
    assert signature_node is not None
    ctx = xmlsec.SignatureContext()
    with urllib.request.urlopen(INCOMMON_METADATA_CERT_URL) as fp:
        key = xmlsec.Key.from_file(fp, xmlsec.constants.KeyDataFormatCertPem)
        ctx.key = key
        ctx.verify(signature_node)


def filter_xml(xml_file_pointer: BytesIO, idp_entity_ids: list[str]):
    idp_records = []
    namespace_keys = []

    for event, element in ET.iterparse(xml_file_pointer, events=("end", "start-ns")):
        if event == "start-ns":
            # Prevent weird clobbering issue
            if element[0] not in namespace_keys:
                namespace_keys.append(element[0])
                ET.register_namespace(*element)
        elif event == "end":
            if "EntityDescriptor" in element.tag and element.attrib["entityID"] in idp_entity_ids:
                idp_records.append(element)

    subtree = ET.ElementTree(element=ET.fromstring(ENTITIES_DESCRIPTOR_ELEMENT))
    subtree.getroot().extend(idp_records)

    return subtree


if __name__ == "__main__":
    assert not sys.stdin.isatty()
    entity_includelist = json.load(sys.stdin)
    with urllib.request.urlopen(INCOMMON_METADATA_URL) as inc_md_download:
        inc_md_data_stream = BytesIO(inc_md_download.read())
        verify_incommon_metadata(inc_md_data_stream)
        inc_md_data_stream.seek(0)  # reset pointer to beginning of file so it can be parsed again
        subtree = filter_xml(inc_md_data_stream, entity_includelist)
        subtree.write(sys.stdout.buffer, encoding="UTF-8", xml_declaration=False, method="xml")
