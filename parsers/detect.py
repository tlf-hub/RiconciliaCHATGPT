from xml.etree import ElementTree as ET
from utils import xml_ns_strip

def detect_xml_kind(root: ET.Element) -> str:
    if xml_ns_strip(root.tag) == "CBIBdySDDReq":
        return "cbi_sdd"
    if root.find(".//{*}FatturaElettronicaBody") is not None:
        return "fatturapa"
    if root.find(".//{*}BkToCstmrStmt") is not None:
        return "camt053"
    if root.find(".//{*}BkToCstmrDbtCdtNtfctn") is not None:
        return "camt054"
    if root.find(".//{*}CstmrDrctDbtInitn") is not None:
        return "pain008"
    return "unknown"
