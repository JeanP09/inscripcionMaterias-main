-- Tabla: Estudiante
CREATE TABLE Estudiante (
    id      SERIAL PRIMARY KEY,
    nombre  TEXT NOT NULL,
    plan_id INTEGER NOT NULL
);

-- Tabla: Curso
CREATE TABLE Curso (
    codigo_alfanumerico VARCHAR(10) PRIMARY KEY,
    nombre              TEXT NOT NULL,
    creditos            INTEGER NOT NULL
);

-- Tabla: NRC
CREATE TABLE NRC (
    id_nrc    INTEGER PRIMARY KEY,
    curso_id  VARCHAR(10) NOT NULL REFERENCES Curso (codigo_alfanumerico),
    jornada   TEXT NOT NULL,
    modalidad TEXT NOT NULL,
    cupo_max  INTEGER NOT NULL
);

-- Tabla: Horario
CREATE TABLE Horario (
    id            SERIAL PRIMARY KEY,
    nrc_id        INTEGER NOT NULL REFERENCES NRC (id_nrc),
    numero_sesion INTEGER NOT NULL,
    dia           INTEGER NOT NULL,
    hora_inicio   TIME NOT NULL,
    hora_fin      TIME NOT NULL
);

-- Tabla: Inscripcion
CREATE TABLE Inscripcion (
    estudiante_id INTEGER NOT NULL REFERENCES Estudiante (id),
    nrc_id        INTEGER NOT NULL REFERENCES NRC (id_nrc),
    fecha         DATE NOT NULL,
    PRIMARY KEY (estudiante_id, nrc_id)
);

-- Consulta: detecta choques de horario (mismo día y horas solapadas) entre NRCs inscritos de un estudiante
SELECT
    i.estudiante_id,
    h1.nrc_id AS nrc_1,
    h2.nrc_id AS nrc_2,
    h1.dia,
    h1.hora_inicio AS inicio_1,
    h1.hora_fin    AS fin_1,
    h2.hora_inicio AS inicio_2,
    h2.hora_fin    AS fin_2
FROM Inscripcion i
JOIN Horario h1 ON h1.nrc_id = i.nrc_id
JOIN Inscripcion i2
     ON i2.estudiante_id = i.estudiante_id
    AND i2.nrc_id > i.nrc_id
JOIN Horario h2
     ON h2.nrc_id = i2.nrc_id
    AND h2.dia = h1.dia
    AND h2.hora_inicio < h1.hora_fin
    AND h1.hora_inicio < h2.hora_fin;
