# Decision Eval Kit

## Концепція

Перетворити `bedrock-agent-eval` на універсальний **evaluation framework для агентних рішень** у різних проєктах. Він перевіряє не лише валідність JSON або факт виклику інструмента, а повний ланцюг: стан → рішення → дія → доказ → спостережуваний результат.

## Єдина модель сценарію

```yaml
scenario:
  input_state: {}
  allowed_tools: []
  expected_decisions: []
  acceptable_actions: []
  forbidden_actions: []
  evidence_requirements: []
  expected_outcome: {}
```

## Рівні оцінювання

- **Schema validity** — чи результат відповідає контракту.
- **Decision correctness** — чи правильне рішення для заданого стану.
- **Tool discipline** — чи використані лише дозволені інструменти й аргументи.
- **Evidence grounding** — чи підтверджене рішення доступними доказами.
- **Outcome correctness** — чи реальна дія дала очікуваний ефект.
- **Safety** — чи не виконано заборонених або незворотних дій.
- **Efficiency** — latency, model calls, tool calls і retries.

## Адаптери проєктів

- Slukhayka: правильна точка semantic rewind і відсутність spoiler leakage.
- PS4 Stream: правильна позиція після перемикання джерела.
- HateTrack: коректний зв’язок між версіями наративу.
- LangSwitcher: точні spans для виправлення.
- PigeonGuard: ефективність обраного deterrent action.
- TL;DV → Jira: правильне виявлення `SUPERSEDES` або `REVERSES`.
- Digest: обґрунтований verdict для старої обіцянки.
- Ads Generator: точна діагностика й контрольований repair.

## MVP

- Винести нейтральний `Scenario` contract із поточного Bedrock-specific коду.
- Додати runner для deterministic fixtures і replay traces.
- Підтримати adapters для моделей, tools та outcome observers.
- Зберігати provenance кожного verdict.
- Реалізувати aggregate report без зведення всього до однієї непрозорої оцінки.
- Перший reference adapter: Decision Archaeology або LangSwitcher.

## Метрики

- Scenario pass rate за окремими dimensions.
- Decision precision/recall.
- Forbidden-action rate.
- Grounded-decision rate.
- Tool-call budget violations.
- Replay reproducibility.
- Regression delta між версіями моделей і prompt/policy.

## Обмеження

Не оцінювати лише фінальний текст. Правильна відповідь після небезпечної або недозволеної дії має вважатися failure. Так само правильний tool call не є успіхом, якщо спостережуваний outcome неправильний.
