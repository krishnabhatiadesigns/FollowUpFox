-- Rank active openings and return supplied email addresses for eligible contacts.
WITH resume_owner AS (
    SELECT id
    FROM resumes
    WHERE id = :resume_id
      AND user_id = :user_id
),
resume_skill_matches AS (
    SELECT skill_id
    FROM resume_skills
    WHERE resume_id = (SELECT id FROM resume_owner)
    UNION
    SELECT job_web_skill.id
    FROM resume_skills AS rs
    JOIN skills AS resume_skill ON resume_skill.id = rs.skill_id
    JOIN skills AS job_web_skill
        ON job_web_skill.name = 'web development' COLLATE NOCASE
    WHERE rs.resume_id = (SELECT id FROM resume_owner)
      AND LOWER(resume_skill.name) IN ('react', 'node.js', 'nodejs', 'javascript', 'typescript')
),
job_skill_matches AS (
    SELECT
        js.job_id,
        COUNT(js.skill_id) AS total_skills,
        SUM(CASE WHEN rs.skill_id IS NOT NULL THEN 1 ELSE 0 END) AS matched_skills
    FROM job_skills AS js
    LEFT JOIN resume_skill_matches AS rs ON rs.skill_id = js.skill_id
    GROUP BY js.job_id
)
SELECT
    j.id AS job_id,
    c.name AS company_name,
    c.hq_city AS company_location,
    j.title AS position,
    ct.id AS contact_id,
    ct.full_name AS hr_name,
    j.experience_level,
    ct.email AS hr_email,
    ct.linkedin_url AS contact_linkedin,
    j.location,
    CASE
        WHEN COALESCE(matches.total_skills, 0) = 0 THEN 0
        ELSE ROUND(100.0 * matches.matched_skills / matches.total_skills, 2)
    END AS skill_match_percentage
FROM jobs AS j
JOIN companies AS c ON c.id = j.company_id
JOIN contacts AS ct ON ct.id = j.contact_id
LEFT JOIN job_skill_matches AS matches ON matches.job_id = j.id
WHERE j.is_active = 1
  AND ct.opted_out = 0
  AND (
      (ct.email IS NOT NULL AND LOWER(ct.email) NOT LIKE '%.invalid')
      OR ct.linkedin_url IS NOT NULL
  )
  AND EXISTS (SELECT 1 FROM resume_owner)
  AND NOT EXISTS (
      SELECT 1
      FROM suppressions AS s
      WHERE s.email = ct.email COLLATE NOCASE
  )
  AND NOT EXISTS (
      SELECT 1
      FROM outreach AS o
      WHERE o.user_id = :user_id
        AND o.contact_id = ct.id
  )
ORDER BY skill_match_percentage DESC, j.posted_at DESC, j.id;
