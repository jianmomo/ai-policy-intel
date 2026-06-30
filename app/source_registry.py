from pathlib import Path
from typing import Any

import yaml

from app.schemas import SourceDefinition


def _read_source_content(config_path: Path) -> dict[str, Any]:
    return yaml.safe_load(config_path.read_text(encoding='utf-8')) or {}


def load_source_definitions(config_path: Path) -> list[SourceDefinition]:
    content = _read_source_content(config_path)
    definitions: list[SourceDefinition] = []
    for source in content.get('sources', []):
        definitions.append(
            SourceDefinition(
                id=source['id'],
                name=source['name'],
                category=source['category'],
                region=source['region'],
                type=source['type'],
                url=source['url'],
                enabled=source.get('enabled', True),
                priority=source.get('priority', 5),
                tags=source.get('tags', []),
                query=source.get('query', ''),
                max_results=source.get('max_results', 10),
                extra=source.get('extra', {}),
            )
        )
    return definitions


def save_source_definitions(config_path: Path, definitions: list[SourceDefinition]) -> None:
    payload = {
        'sources': [
            {
                'id': definition.id,
                'name': definition.name,
                'category': definition.category,
                'region': definition.region,
                'type': definition.type,
                'url': definition.url,
                'enabled': definition.enabled,
                'priority': definition.priority,
                'tags': definition.tags,
                'query': definition.query,
                'max_results': definition.max_results,
                'extra': definition.extra,
            }
            for definition in definitions
        ]
    }
    config_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding='utf-8')


def update_source_definition(config_path: Path, source_id: str, **changes: Any) -> SourceDefinition:
    definitions = load_source_definitions(config_path)
    updated: SourceDefinition | None = None
    for definition in definitions:
        if definition.id != source_id:
            continue
        for key, value in changes.items():
            if hasattr(definition, key):
                setattr(definition, key, value)
        updated = definition
        break
    if updated is None:
        raise KeyError(source_id)
    save_source_definitions(config_path, definitions)
    return updated
