import json
import logging
import os
import re
import warnings
from typing import Any, Dict, List

warnings.filterwarnings('ignore', message='.*PydanticSerializationUnexpectedValue.*', category=UserWarning)

from openai import AzureOpenAI

from src.config.backstory import AGENT_BACKSTORY
from src.config.task import build_task_description

logger = logging.getLogger(__name__)

COLUMNS = [
    'Header', 'Service', 'In-Network Coinsurance', 'In-Network After Deductible Flag',
    'In-Network Copay', 'Out-Of-Network Coinsurance', 'Out-Of-Network After Deductible Flag',
    'Out-Of-Network Copay', 'Individual In-Network', 'Family In-Network',
    'Individual Out-Of-Network', 'Family Out-Of-Network',
    'Limit Type', 'Limit Period', 'Pre-Authorization Required', 'Confidence Score',
]


def _chunk_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def _parse_json_from_output(raw: str) -> List[Dict[str, Any]]:
    bt3 = chr(96) * 3
    raw = re.sub(bt3 + r'(?:json)?', '', raw).strip()
    start = raw.find('[')
    end   = raw.rfind(']')
    if start == -1 or end == -1:
        logger.warning('No JSON array found. Snippet: %s', raw[:300])
        return []
    json_str = raw[start : end + 1]
    try:
        data = json.loads(json_str)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError as exc:
        logger.warning('JSON decode failed (%s). Repairing...', exc)
    json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
    try:
        data = json.loads(json_str)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    logger.error('Could not parse JSON from LLM output.')
    return []


def _normalise(s: str) -> str:
    """Collapse all whitespace variants so 'Out-of- Pocket' == 'Out-of-Pocket'."""
    import re
    return re.sub(r'\s+', ' ', str(s)).strip().lower()

def _richness(rec: Dict[str, Any]) -> int:
    """Count non-empty fields — used to prefer the more populated record."""
    return sum(1 for v in rec.values() if str(v).strip())


def _deduplicate(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Deduplicate by (Header, Service) with whitespace-normalised keys.
    When the same key is seen more than once keep the RICHER record
    (most populated fields), so structured-table rows with both In-Network
    and Out-of-Network values beat raw-OCR rows that only have one column.
    """
    best: Dict[tuple, Dict[str, Any]] = {}
    for rec in records:
        key = (
            _normalise(rec.get('Header',  '')),
            _normalise(rec.get('Service', '')),
        )
        if key not in best or _richness(rec) > _richness(best[key]):
            best[key] = rec
    return list(best.values())


def _build_client() -> AzureOpenAI:
    api_key     = os.getenv('AZURE_OPENAI_API_KEY')
    endpoint    = os.getenv('AZURE_OPENAI_ENDPOINT', '').strip()
    api_version = os.getenv('AZURE_OPENAI_API_VERSION', '2025-01-01-preview')
    if not api_key or not endpoint:
        raise ValueError('AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT must be set.')
    logger.debug('AzureOpenAI client -- endpoint: %s  api_version: %s', endpoint, api_version)
    return AzureOpenAI(api_key=api_key, azure_endpoint=endpoint, api_version=api_version)


def _call_gpt(client: AzureOpenAI, chunk: str) -> str:
    deployment = os.getenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4o')
    temp       = float(os.getenv('AZURE_OPENAI_TEMPERATURE', '0.1'))
    timeout    = float(os.getenv('AZURE_OPENAI_TIMEOUT', '800'))
    response = client.chat.completions.create(
        model=deployment,
        messages=[
            {'role': 'system', 'content': AGENT_BACKSTORY},
            {'role': 'user',   'content': build_task_description(chunk)},
        ],
        temperature=temp,
        timeout=timeout,
    )
    return response.choices[0].message.content or ''


def run_benefits_extraction_crew(text_content: str, output_excel_path: str) -> List[Dict[str, Any]]:
    from src.generators.excel_generator import ExcelGenerator
    chunk_size = int(os.getenv('CHUNK_SIZE',    '25000'))
    overlap    = int(os.getenv('CHUNK_OVERLAP', '500'))
    chunks = _chunk_text(text_content, chunk_size, overlap)
    logger.info('Extraction: %d chunk(s) to process', len(chunks))
    client = _build_client()
    all_records: List[Dict[str, Any]] = []
    for i, chunk in enumerate(chunks, 1):
        logger.info('Processing chunk %d / %d ...', i, len(chunks))
        try:
            raw_output = _call_gpt(client, chunk)
            records    = _parse_json_from_output(raw_output)
            logger.info('Chunk %d: %d records extracted', i, len(records))
            all_records.extend(records)
        except Exception as exc:
            logger.error('Chunk %d failed: %s', i, exc, exc_info=True)
    all_records = _deduplicate(all_records)
    logger.info('Total unique records after deduplication: %d', len(all_records))
    if not all_records:
        logger.warning('No records extracted -- Excel file will not be created.')
        return []
    normalised = _normalise_records(all_records)
    generator = ExcelGenerator(output_excel_path)
    generator.generate_from_json(normalised, include_metadata=True)
    logger.info('Excel written to %s', output_excel_path)
    return normalised


_KEY_ALIASES: Dict[str, str] = {
    'in-network after deductible':           'In-Network After Deductible Flag',
    'in network after deductible':           'In-Network After Deductible Flag',
    'in-network after deductible flag':      'In-Network After Deductible Flag',
    'out-of-network after deductible':       'Out-Of-Network After Deductible Flag',
    'out of network after deductible':       'Out-Of-Network After Deductible Flag',
    'out-of-network after deductible flag':  'Out-Of-Network After Deductible Flag',
    'in-network coinsurance':                'In-Network Coinsurance',
    'in network coinsurance':                'In-Network Coinsurance',
    'out-of-network coinsurance':            'Out-Of-Network Coinsurance',
    'out of network coinsurance':            'Out-Of-Network Coinsurance',
    'in-network copay':                      'In-Network Copay',
    'in network copay':                      'In-Network Copay',
    'out-of-network copay':                  'Out-Of-Network Copay',
    'out of network copay':                  'Out-Of-Network Copay',
    'individual in-network':                 'Individual In-Network',
    'individual in network':                 'Individual In-Network',
    'family in-network':                     'Family In-Network',
    'family in network':                     'Family In-Network',
    'individual out-of-network':             'Individual Out-Of-Network',
    'individual out of network':             'Individual Out-Of-Network',
    'family out-of-network':                 'Family Out-Of-Network',
    'family out of network':                 'Family Out-Of-Network',
    'pre-authorization required':            'Pre-Authorization Required',
    'preauthorization required':             'Pre-Authorization Required',
    'prior authorization required':          'Pre-Authorization Required',
    'pre authorization required':            'Pre-Authorization Required',
    'confidence':                            'Confidence Score',
    'confidence score':                      'Confidence Score',
}


def _normalise_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for rec in records:
        normalised: Dict[str, Any] = {}
        for k, v in rec.items():
            mapped = _KEY_ALIASES.get(k.lower().strip(), k)
            normalised[mapped] = v
        out.append(normalised)
    return out
