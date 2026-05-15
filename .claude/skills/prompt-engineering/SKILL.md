---
name: prompt-engineering
description: Patterns for building and tuning AI prompts in TraceFlow — lead qualification, knowledge base responses, AI greetings, owner alert classification, field mapping suggestions. Load when writing new prompts, debugging prompt failures, or generalizing client-specific prompts into templated ones.
---

# Prompt Engineering

TraceFlow uses LLMs at several touchpoints. All prompts are **templated with variable slots filled from `client_configs`** — never hardcoded per client.

## Prompt taxonomy

| Prompt | Purpose | Model | Avg tokens |
|---|---|---|---|
| `greeting` | First SMS after missed call | Haiku/4o-mini (cheap) | ~150 |
| `qualifier` | Multi-turn lead qualification via SMS | Sonnet 4.6 | ~500-2000 |
| `vip_classifier` | Decide if owner gets alerted | Haiku/4o-mini | ~200 |
| `kb_responder` | Knowledge base Q&A (Module C) | Sonnet 4.6 + pgvector retrieval | ~1500 |
| `field_mapper` | Internal: suggest field mappings during onboarding | Sonnet 4.6 | ~3000 |
| `digest_writer` | Daily owner digest copy | Haiku/4o-mini | ~800 |

## The templating system

Every prompt is a Jinja2-style template stored in code, populated with variables from `client_configs.brand`, `client_configs.business_hours`, etc.

```python
# app/prompts/greeting.py

GREETING_TEMPLATE = """\
You are responding on behalf of {{business_name}}, a {{business_category}} business serving {{service_area}}.

Tone: {{tone_of_voice}}
Caller's number: {{caller_phone}}
Time of call: {{call_time}} ({{business_hours_status}})

Task: write a short SMS (under 160 characters) that:
1. Apologizes for missing their call
2. Identifies your business
3. Asks what they need help with
4. Sounds {{tone_of_voice}}

Do not use emojis unless the tone is "casual". Do not promise specific response times. Do not give pricing.

Respond with ONLY the SMS body — no quotes, no commentary.
"""

async def generate_greeting(client_config: ClientConfig, caller_phone: str) -> str:
    template = jinja2.Template(GREETING_TEMPLATE)
    rendered = template.render(
        business_name=client_config.business_name,
        business_category=client_config.brand.get("category", "service business"),
        service_area=", ".join(client_config.service_area_zips[:3]) or "your area",
        tone_of_voice=client_config.brand.get("tone_of_voice", "friendly and professional"),
        caller_phone=caller_phone,
        call_time=now_in_tz(client_config.timezone),
        business_hours_status=is_within_business_hours(client_config),
    )
    
    response = await anthropic_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        messages=[{"role": "user", "content": rendered}],
    )
    return response.content[0].text.strip()
```

## The qualifier prompt (most complex)

The qualifier runs multi-turn until it has enough data to qualify or disqualify. Each turn gets the full conversation history + business context.

```python
QUALIFIER_SYSTEM = """\
You are a friendly intake assistant for {{business_name}}, a {{business_category}} business in {{service_area}}.

Your goal: collect enough information to qualify this lead. Extract these fields when natural:
- service_type (one of: {{service_types | join(", ")}})
- sqft (project size, if applicable)
- budget_range (one of: <5k, 5k-15k, 15k-50k, 50k+)
- timeframe (one of: asap, this_month, this_quarter, researching)
- address or service area

Rules:
- ONE question per message. Never more.
- Keep messages under 160 characters (SMS).
- Sound {{tone_of_voice}}.
- Don't ask for budget directly — infer from project size + service type when possible.
- If they mention a competitor, do NOT badmouth.
- If they ask for pricing, redirect: "I'll have someone reach out with specifics."
- VIP signals (alert the owner): {{vip_keywords | join(", ")}}
- After 4-5 messages, if you have qualification data, wrap up gracefully.
- If they're clearly not a fit (wrong location, wrong service, residential vs commercial mismatch), thank them politely and end.

End every message naturally. Don't sign off with the business name every time.

You have access to a function `update_lead(field, value)` that stores extracted data.
"""

async def qualifier_turn(
    config: ClientConfig,
    conversation_history: list[Message],
    new_message: str,
) -> tuple[str, dict]:
    """Process one turn of qualification. Returns (response_text, extracted_fields)."""
    
    system = jinja2.Template(QUALIFIER_SYSTEM).render(
        business_name=config.business_name,
        business_category=config.brand.get("category"),
        service_area=", ".join(config.service_area_zips[:3]),
        service_types=config.brand.get("service_types", []),
        tone_of_voice=config.brand.get("tone_of_voice", "friendly"),
        vip_keywords=config.vip_keywords,
    )
    
    messages = [
        {"role": "user" if m.direction == "inbound" else "assistant", "content": m.body}
        for m in conversation_history
    ] + [{"role": "user", "content": new_message}]
    
    response = await anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=system,
        messages=messages,
        tools=[UPDATE_LEAD_TOOL],
    )
    
    # Extract any tool calls (field updates)
    extracted = {}
    response_text = ""
    for block in response.content:
        if block.type == "tool_use" and block.name == "update_lead":
            extracted[block.input["field"]] = block.input["value"]
        elif block.type == "text":
            response_text = block.text
    
    return response_text, extracted
```

## Prompt template best practices

### Do

- **Use Jinja2 variables for every client-specific value.** Business name, tone, service area, hours, all of it.
- **Test prompts with golden conversations.** Maintain a `tests/prompts/golden/` directory with expected behaviors.
- **Include negative examples.** Tell the model what NOT to do explicitly.
- **Constrain output format strictly.** Models obey constraints better than they self-edit.
- **Use cheap models (Haiku) for stateless single-turn prompts.** Sonnet for stateful, multi-turn, or complex.

### Don't

- **Don't put PII in prompts unnecessarily.** Hash phone numbers when they're identifiers, not content.
- **Don't write prompts longer than they need to be.** Token costs scale per request × per client.
- **Don't embed dates inline ("today is...").** Pass them as variables; model will see them at runtime correctly.
- **Don't use temperature > 0.5 for qualification.** Determinism matters.
- **Don't trust the model to keep secrets.** If a value shouldn't leak in SMS, don't put it in the prompt.

## Versioning prompts

Prompts evolve. Track versions explicitly:

```python
GREETING_TEMPLATE_V2 = """\
... new version ...
"""

PROMPT_VERSIONS = {
    "greeting": {
        "v1": GREETING_TEMPLATE_V1,
        "v2": GREETING_TEMPLATE_V2,
    },
}
```

Per-client config can pin a version:

```sql
update client_configs
set prompt_versions = jsonb_set(prompt_versions, '{greeting}', '"v2"')
where client_id = '...';
```

This lets you A/B test new versions before rolling out universally.

## Golden test pattern

For every prompt, maintain a fixture of expected behaviors:

```python
# tests/prompts/test_qualifier.py

@pytest.mark.parametrize("scenario", load_golden_scenarios("qualifier"))
async def test_qualifier_golden(scenario):
    """Verify qualifier behaves correctly on canonical scenarios."""
    config = scenario.client_config
    conversation = scenario.conversation_history
    new_message = scenario.new_message
    
    response, extracted = await qualifier_turn(config, conversation, new_message)
    
    # Assertions on response text
    if scenario.expected_response_contains:
        for substring in scenario.expected_response_contains:
            assert substring.lower() in response.lower()
    
    # Assertions on extracted fields
    for field, expected_value in scenario.expected_extractions.items():
        assert extracted.get(field) == expected_value
    
    # Assertions on response length
    assert len(response) <= 160, "SMS too long"
```

Golden scenarios go in `tests/prompts/golden/qualifier/*.yaml`:

```yaml
name: countertop_residential_qualified
client_config:
  business_name: "Vegas Surfaces"
  service_types: ["countertop", "flooring"]
conversation_history:
  - direction: outbound
    body: "Hey, this is Vegas Surfaces - sorry we missed your call! What can we help with?"
  - direction: inbound
    body: "Hi, looking to redo my kitchen countertops, maybe 40 sqft"
new_message: "Hi, looking to redo my kitchen countertops, maybe 40 sqft"
expected_extractions:
  service_type: "countertop"
  sqft: 40
expected_response_contains:
  - "?"  # asks a follow-up question
```

## When to escalate to Claude

If a prompt is consistently failing despite tuning, escalate to a session with Claude where you:
1. Show the failing conversation transcripts
2. Show the current prompt
3. Describe the desired behavior
4. Ask for systematic improvements, not single-edit patches

Better to do one informed rewrite than ten reactive tweaks.

## Cost monitoring

Per-client AI usage is tracked in `client_configs.ai_interactions_used`. Hit 80% of cap → notify founder + client. Hit 100% → graceful degradation:

- Continue accepting messages (don't drop leads)
- Use cheapest model (Haiku) regardless of configured tier
- Mark conversations as "over-cap" in logs for review

Never silently fail an interaction due to cost limits. Leads are too valuable.
