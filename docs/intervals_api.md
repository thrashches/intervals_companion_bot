# Intervals.icu API — шпаргалка

Базовый URL: `https://intervals.icu/api/v1`

## Auth

Personal API key (Settings → Developer Settings):

```
Basic Auth
username: API_KEY
password: <your_api_key>
```

Athlete id в path: `0` = текущий атлет по ключу.

## Эндпоинты, которые использует проект

| Назначение | Запрос |
|------------|--------|
| Профиль / валидация | `GET /athlete/0` |
| Календарь | `GET /athlete/0/events?oldest=YYYY-MM-DD&newest=YYYY-MM-DD&resolve=true` |
| Wellness / форма | `GET /athlete/0/wellness?oldest=&newest=` |
| Fitness series | `GET /athlete/0/fitness?oldest=&newest=` |
| Активности | `GET /athlete/0/activities?oldest=&newest=` |
| Детали + интервалы | `GET /activity/{id}?intervals=true` |
| Power curves | `GET /athlete/0/power-curves?curves=all&type=Ride` |
| HR curves | `GET /athlete/0/hr-curves?curves=all&type=Ride` |

Параметр `curves`: `1y`, `42d`, `s0`, `all`, или диапазон `r.YYYY-MM-DD.YYYY-MM-DD`.
В ответе кривой — параллельные массивы `secs`, `values`, `activity_id`: если `activity_id[i]` совпадает с текущей активностью на ключевой длительности (5с…60м), это личный рекорд.

## Поля формы (wellness)

Клиент ищет (в порядке приоритета):

- fitness: `icu_ctl`, `ctl`, `fitness`
- fatigue: `icu_atl`, `atl`, `fatigue` (субъективный `fatigue` — последний)
- form: `icu_form`, `form`, `tsb`, `icu_tsb`; если нет — **CTL − ATL**
- weight: последний непустой `weight` / `icu_weight` за окно wellness, иначе `icu_weight` из профиля атлета
- vo2max: последний непустой `vo2max` / `vo2_max` / `icu_vo2max` за окно wellness

На заметку: в дневном wellness вес и VO2max часто `null` в дни без нового измерения — их нужно брать из более ранних записей.

## Дневной wellness (WellnessDay)

При sync формы строки wellness также сохраняются по дням для AI-анализа. Поля:

| Поле у нас | ICU |
|------------|-----|
| `sleep_secs` | `sleepSecs` |
| `sleep_quality` / `sleep_score` | `sleepQuality` / `sleepScore` |
| `resting_hr` | `restingHR` |
| `avg_sleeping_hr` | `avgSleepingHR` |
| `hrv` | `hrv` / `hrvSDNN` |
| `weight` | `weight` |
| `fatigue` | `fatigue` (субъективный) |
| `soreness`, `stress`, `mood`, `motivation` | одноимённые |
| `injury`, `readiness` | `injury`, `readiness` |
| `fitness` / `fatigue_atl` / `form` | `icu_ctl` / `icu_atl` / form |

## Документация

- Forum guide: https://forum.intervals.icu/t/api-access-to-intervals-icu/609
- Cookbook: https://forum.intervals.icu/t/intervals-icu-api-integration-cookbook/80090
- OpenAPI UI: https://intervals.icu/api-docs.html
