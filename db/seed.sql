PRAGMA foreign_keys = ON;

INSERT OR IGNORE INTO companies (id, name, domain, industry, size_band, hq_city) VALUES
    (1, 'Northstar Analytics', 'northstar.example', 'Technology', 'startup', 'Austin'),
    (2, 'Cedar Labs', 'cedarlabs.example', 'Software', 'small', 'Seattle'),
    (3, 'Brightpath Health', 'brightpath.example', 'Healthcare', 'medium', 'Boston'),
    (4, 'Harbor Finance', 'harborfinance.example', 'Financial services', 'large', 'New York'),
    (5, 'Summit Works', 'summitworks.example', 'Consulting', 'medium', 'Denver');

INSERT OR IGNORE INTO contacts
    (id, company_id, full_name, job_title, email, source)
VALUES
    (1, 1, 'Avery Morgan', 'People Operations Manager', 'avery@northstar.example', 'company_website'),
    (2, 2, 'Jordan Lee', 'Technical Recruiter', 'jordan@cedarlabs.example', 'company_website'),
    (3, 3, 'Priya Shah', 'Talent Acquisition Partner', 'priya@brightpath.example', 'company_website'),
    (4, 4, 'Morgan Chen', 'HR Business Partner', 'morgan@harborfinance.example', 'company_website'),
    (5, 5, 'Taylor Brooks', 'Campus Recruiting Lead', 'taylor@summitworks.example', 'company_website');

INSERT OR IGNORE INTO jobs
    (id, company_id, contact_id, title, description, location, work_mode, experience_level, min_salary, max_salary)
VALUES
    (1, 1, 1, 'Junior Data Analyst', 'Analyze product data and communicate actionable findings.', 'Austin, TX', 'hybrid', 'junior', 65000, 85000),
    (2, 1, 1, 'Machine Learning Intern', 'Build and evaluate machine learning prototypes with the data team.', 'Austin, TX', 'onsite', 'intern', 25000, 35000),
    (3, 2, 2, 'Frontend Developer', 'Build accessible web interfaces for developer tools.', 'Seattle, WA', 'remote', 'fresher', 70000, 95000),
    (4, 2, 2, 'Software Engineering Intern', 'Contribute tested features to the product engineering team.', 'Seattle, WA', 'hybrid', 'intern', 30000, 40000),
    (5, 3, 3, 'Product Analyst', 'Use SQL and dashboards to support healthcare product decisions.', 'Boston, MA', 'hybrid', 'junior', 68000, 88000),
    (6, 3, 3, 'UX Research Intern', 'Plan interviews and synthesize user research findings.', 'Boston, MA', 'onsite', 'intern', 24000, 32000),
    (7, 4, 4, 'Data Engineer', 'Develop reliable data pipelines and warehouse models.', 'New York, NY', 'hybrid', 'mid', 100000, 135000),
    (8, 5, 5, 'Business Analyst Fresher', 'Support client teams with research, analysis, and presentations.', 'Denver, CO', 'onsite', 'fresher', 60000, 78000);

INSERT OR IGNORE INTO skills (id, name, category) VALUES
    (1, 'Python', 'programming'),
    (2, 'SQL', 'data'),
    (3, 'Machine Learning', 'data'),
    (4, 'Pandas', 'data'),
    (5, 'JavaScript', 'programming'),
    (6, 'React', 'frontend'),
    (7, 'TypeScript', 'programming'),
    (8, 'HTML/CSS', 'frontend'),
    (9, 'Git', 'tools'),
    (10, 'Data Visualization', 'data'),
    (11, 'User Research', 'design'),
    (12, 'Figma', 'design'),
    (13, 'Communication', 'soft skills'),
    (14, 'Excel', 'tools'),
    (15, 'Statistics', 'data');

INSERT OR IGNORE INTO job_skills (job_id, skill_id, is_required) VALUES
    (1, 2, 1), (1, 10, 1), (1, 14, 0), (1, 13, 1),
    (2, 1, 1), (2, 3, 1), (2, 4, 1), (2, 15, 1),
    (3, 5, 1), (3, 6, 1), (3, 7, 0), (3, 8, 1), (3, 9, 0),
    (4, 1, 0), (4, 5, 1), (4, 9, 1), (4, 13, 1),
    (5, 2, 1), (5, 10, 1), (5, 15, 1), (5, 13, 0),
    (6, 11, 1), (6, 12, 0), (6, 13, 1),
    (7, 1, 1), (7, 2, 1), (7, 9, 1), (7, 15, 0),
    (8, 2, 0), (8, 13, 1), (8, 14, 1), (8, 10, 0);
