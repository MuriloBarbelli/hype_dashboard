CREATE OR REPLACE VIEW public.vw_resident_classification AS
WITH entradas AS (
    SELECT
        events.user_name,
        events.user_profile,
        events.unit,
        date(events.event_timestamp)                          AS dia,
        date_trunc('month', events.event_timestamp)::date     AS mes
    FROM events
    WHERE events.event_type_code = ANY(ARRAY[701, 708, 311, 183])
      AND events.access_name = ANY(ARRAY[
          '6062 Portão Pedestre Interno',
          '6064 Hall Residencial',
          '6061 Portão Pedestre Externo',
          '6063 Hall NR'
      ])
      AND events.user_name IS NOT NULL AND events.user_name <> ''
      AND events.unit      IS NOT NULL AND events.unit      <> ''
      AND events.unit NOT LIKE '%999%'
),
-- perfis únicos de cada pessoa na unidade, concatenados
perfis_por_pessoa AS (
    SELECT user_name, unit,
           string_agg(DISTINCT user_profile, ' · ' ORDER BY user_profile) AS user_profile
    FROM entradas
    GROUP BY user_name, unit
),
-- dias agrupados por (user_name, unit, mes) — SEM user_profile,
-- para somar presença de todos os perfis da mesma pessoa no mesmo mês
presenca_mensal AS (
    SELECT user_name, unit, mes,
           count(DISTINCT dia) AS dias_no_mes
    FROM entradas
    GROUP BY user_name, unit, mes
),
-- uma linha por (user_name, unit), com user_profile concatenado
resumo AS (
    SELECT
        pm.user_name,
        pp.user_profile,
        pm.unit,
        count(DISTINCT pm.mes)                                            AS total_meses,
        min(pm.mes)                                                       AS primeiro_mes,
        max(pm.mes)                                                       AS ultimo_mes,
        round(avg(pm.dias_no_mes), 1)                                     AS media_dias_por_mes,
        sum(CASE WHEN pm.dias_no_mes >= 13 THEN 1 ELSE 0 END)            AS meses_residente,
        sum(CASE WHEN pm.dias_no_mes BETWEEN 3 AND 12 THEN 1 ELSE 0 END) AS meses_fraco,
        sum(CASE WHEN pm.dias_no_mes < 3 THEN 1 ELSE 0 END)             AS meses_ausente,
        max(pm.dias_no_mes)                                               AS pico_dias
    FROM presenca_mensal pm
    JOIN perfis_por_pessoa pp ON pp.user_name = pm.user_name AND pp.unit = pm.unit
    GROUP BY pm.user_name, pm.unit, pp.user_profile
),
meses_calendario AS (
    SELECT DISTINCT mes
    FROM presenca_mensal
    WHERE mes >= date_trunc('month', now() - INTERVAL '3 months')::date
      AND mes <  date_trunc('month', now())::date
),
-- recente: uma linha por (user_name, unit) — presenca_mensal já tem 1 linha/mês
recente AS (
    SELECT
        r.user_name,
        r.unit,
        count(CASE WHEN coalesce(pm.dias_no_mes, 0) >= 13 THEN 1 ELSE NULL END) AS meses_residente_recente,
        count(CASE WHEN coalesce(pm.dias_no_mes, 0) < 3  THEN 1 ELSE NULL END) AS meses_ausente_recente,
        max(pm.mes)                                                               AS ultimo_mes_com_entrada
    FROM resumo r
    CROSS JOIN meses_calendario mc
    LEFT JOIN presenca_mensal pm
           ON pm.user_name = r.user_name AND pm.unit = r.unit AND pm.mes = mc.mes
    GROUP BY r.user_name, r.unit
),
setor AS (
    SELECT DISTINCT unit,
        CASE
            WHEN substring(unit, '\d+')::integer BETWEEN 100 AND 399 THEN 'NR'
            ELSE 'Residencial'
        END AS setor_condominio
    FROM entradas
    WHERE unit ~ '\d+'
),
classificacao AS (
    SELECT
        r.user_name,
        r.user_profile,
        r.unit,
        s.setor_condominio,
        r.total_meses,
        r.primeiro_mes,
        r.ultimo_mes,
        r.media_dias_por_mes,
        r.meses_residente,
        r.meses_fraco,
        r.meses_ausente,
        r.pico_dias,
        coalesce(rc.meses_residente_recente, 0) AS meses_residente_recente,
        coalesce(rc.meses_ausente_recente,   0) AS meses_ausente_recente,
        coalesce(rc.ultimo_mes_com_entrada, r.ultimo_mes) AS ultimo_mes_com_entrada,
        CASE
            WHEN r.total_meses <= 2
                 AND r.meses_residente >= 1
                 AND r.primeiro_mes >= date_trunc('month', now() - INTERVAL '2 months')::date
                 THEN 'novo_morador'
            WHEN coalesce(rc.meses_residente_recente, 0) >= 1
                 THEN 'residente_ativo'
            WHEN r.meses_residente >= 2
                 AND coalesce(rc.meses_ausente_recente,   0) BETWEEN 1 AND 3
                 AND coalesce(rc.meses_residente_recente, 0) = 0
                 THEN 'em_viagem'
            WHEN r.media_dias_por_mes >= 6
                 THEN 'presenca_regular'
            ELSE 'visitante'
        END AS classificacao_auto,
        -- alertas: LIKE para suportar perfis concatenados (ex: 'Morador · Convidado')
        CASE
            WHEN r.user_profile LIKE '%Convidado%'
                 AND coalesce(rc.meses_residente_recente, 0) >= 1
                 AND r.user_profile NOT LIKE '%Morador%'
                 THEN 'Convidado com comportamento de morador'
            WHEN r.user_profile LIKE '%Familiar%'
                 AND coalesce(rc.meses_residente_recente, 0) >= 1
                 AND r.user_profile NOT LIKE '%Morador%'
                 THEN 'Familiar com comportamento de morador'
            WHEN (r.user_profile LIKE '%Morador%' OR r.user_profile LIKE '%Proprietário%')
                 AND r.media_dias_por_mes < 3
                 AND coalesce(rc.meses_residente_recente, 0) = 0
                 THEN 'Morador com presença mínima'
            WHEN r.total_meses <= 2
                 AND r.meses_residente >= 1
                 AND r.primeiro_mes >= date_trunc('month', now() - INTERVAL '2 months')::date
                 THEN 'Novo residente detectado'
            ELSE NULL
        END AS alerta
    FROM resumo r
    LEFT JOIN recente rc ON rc.user_name = r.user_name AND rc.unit = r.unit
    LEFT JOIN setor   s  ON s.unit       = r.unit
)
SELECT
    c.user_name,
    c.user_profile,
    c.unit,
    c.setor_condominio,
    c.total_meses,
    c.primeiro_mes,
    c.ultimo_mes,
    c.media_dias_por_mes,
    c.meses_residente,
    c.meses_fraco,
    c.meses_ausente,
    c.pico_dias,
    c.meses_residente_recente,
    c.meses_ausente_recente,
    c.ultimo_mes_com_entrada,
    c.classificacao_auto,
    c.alerta,
    rr.classification AS classificacao_manual,
    rr.confidence,
    rr.notes          AS notas_revisao,
    rr.reviewed_at,
    rr.reviewed_by,
    coalesce(rr.classification, c.classificacao_auto) AS classificacao_efetiva
FROM classificacao c
LEFT JOIN (
    SELECT DISTINCT ON (user_name, unit)
        user_name, unit, classification, confidence, notes, reviewed_at, reviewed_by
    FROM resident_review
    ORDER BY user_name, unit, reviewed_at DESC
) rr ON rr.user_name = c.user_name AND rr.unit = c.unit
ORDER BY
    CASE c.classificacao_auto
        WHEN 'novo_morador'    THEN 1
        WHEN 'residente_ativo' THEN 2
        WHEN 'em_viagem'       THEN 3
        WHEN 'presenca_regular'THEN 4
        ELSE 5
    END,
    c.unit, c.user_name;
