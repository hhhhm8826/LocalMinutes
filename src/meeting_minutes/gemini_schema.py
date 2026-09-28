"""Translate canonical schemas to Gemini's documented structured-output subset."""
from .ai_common import AIFailure


def wire_schema(schema):
    definitions = schema.get('$defs', {})
    allowed = {'type','description','enum','format','minimum','maximum','minItems','maxItems','additionalProperties'}
    def walk(node, seen=()):
        if not isinstance(node, dict):
            return node
        if '$ref' in node:
            ref=node['$ref']
            if not ref.startswith('#/$defs/') or ref in seen or ref[8:] not in definitions:
                raise AIFailure('AI_SCHEMA_UNSUPPORTED')
            return walk(definitions[ref[8:]],(*seen,ref))
        if 'anyOf' in node:
            variants=node['anyOf']
            nonnull=[item for item in variants if item.get('type')!='null']
            if len(variants)!=2 or len(nonnull)!=1:
                raise AIFailure('AI_SCHEMA_UNSUPPORTED')
            value=walk(nonnull[0],seen)
            if not isinstance(value.get('type'),str):
                raise AIFailure('AI_SCHEMA_UNSUPPORTED')
            value['type']=[value['type'],'null']
            return value
        result={key:value for key,value in node.items() if key in allowed}
        if 'const' in node:
            result['enum']=[node['const']]
        if 'properties' in node:
            result['properties']={key:walk(value,seen) for key,value in node['properties'].items()}
        if 'required' in node:
            result['required']=list(node['required'])
        if 'items' in node:
            result['items']=walk(node['items'],seen)
        if isinstance(result.get('additionalProperties'),dict):
            result['additionalProperties']=walk(result['additionalProperties'],seen)
        return result
    return walk(schema)


def schema_instruction(schema):
    """Server-owned schema precedes untrusted transcript content in JSON mode."""
    import json
    return 'Return one JSON object conforming to this JSON Schema. Do not add markdown fences.\n' + json.dumps(schema,ensure_ascii=False,separators=(',', ':')) + '\n'
