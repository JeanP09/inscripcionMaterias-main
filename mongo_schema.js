// ============================================================
// mongo_schema.js
// Esquema NoSQL (MongoDB) del Sistema de Inscripción de Materias.
//
// El proyecto ahora es Data-Driven:
//   * catalogo_materias -> la llena el Data Lake desde CSV. Aquí se crea
//                          VACÍA y solo se declara/valida su estructura.
//   * reglas_negocio    -> la mantiene el Administrador (prerrequisitos
//                          dinámicos). Es la fuente de verdad que el script
//                          `actualizar_grafo.py` usa para sincronizar Neo4j.
//
// Uso:
//   mongosh "mongodb://localhost:27017/inscripcion" mongo_schema.js
//
// El script es idempotente: puede ejecutarse varias veces sin efectos
// adversos (crea las colecciones solo si no existen, reaplica los
// validadores y los índices). NO inserta datos: las colecciones quedan
// vacías a la espera del Data Lake / del Administrador.
// ============================================================

const dbName = "inscripcion"; // DB por defecto (coincide con docker-compose.yml).
db = db.getSiblingDB(dbName);

// ------------------------------------------------------------
// Utilidad: asegura que una colección exista y le aplica un
// validador $jsonSchema. Es idempotente (usa createCollection si
// falta + collMod para reaplicar el validador sin duplicar).
// ------------------------------------------------------------
function asegurarColeccion(nombre, schema) {
  if (!db.getCollectionNames().includes(nombre)) {
    db.createCollection(nombre);
  }
  db.runCommand({
    collMod: nombre,
    validator: { $jsonSchema: schema },
    validationLevel: "moderate", // solo valida docs nuevos/modificados
    validationAction: "error",
  });
  print(`  Colección '${nombre}' lista (validador aplicado).`);
}

// ------------------------------------------------------------
// 1) catalogo_materias — catálogo (VACÍO) que llenará el Data Lake.
//
// Estructura esperada de cada documento:
//   {
//     codigo: "CAL-101",               // string — código único
//     nombre: "Cálculo I",             // string — nombre legible
//     creditos: 4,                     // number — créditos
//     semestre: 1,                     // number — semestre sugerido del plan
//     prerrequisitos: [],              // array<string> — códigos prerequisito
//     plan: "Ingeniería de Sistemas"   // string (opcional) — plan de estudios
//   }
//
// Nota: si el Data Lake escribe `creditos`/`semestre` como string
// (típico al leer CSV sin castear), hay que tiparlos en el ETL o
// relajar el validador (cambiar bsonType:"number" por ["number","string"]).
// ------------------------------------------------------------
print("== catalogo_materias ==");
asegurarColeccion("catalogo_materias", {
  bsonType: "object",
  required: ["codigo", "nombre", "creditos", "semestre", "prerrequisitos"],
  properties: {
    codigo: {
      bsonType: "string",
      description: "Código único de la materia (ej. 'CAL-101')",
    },
    nombre: {
      bsonType: "string",
      description: "Nombre legible de la materia",
    },
    creditos: {
      bsonType: "number",
      description: "Créditos de la materia",
    },
    semestre: {
      bsonType: "number",
      description: "Semestre sugerido en el plan de estudios",
    },
    prerrequisitos: {
      bsonType: "array",
      description: "Códigos de las materias que deben cursarse antes",
      items: { bsonType: "string" },
    },
    plan: {
      bsonType: ["string", "null"],
      description: "Plan de estudios al que pertenece la materia (opcional)",
    },
  },
});

// Índice único sobre `codigo` (clave natural del catálogo).
db.catalogo_materias.createIndex(
  { codigo: 1 },
  { unique: true, name: "uq_catalogo_codigo" }
);
print("  Índice único 'uq_catalogo_codigo' sobre {codigo:1} OK.");

// ------------------------------------------------------------
// 2) reglas_negocio — prerrequisitos dinámicos.
//
// Un documento por materia; el array `prerrequisitos` es la lista
// EXACTA de códigos que la materia exige. Ejemplo:
//   { materia: "CAL-201", prerrequisitos: ["CAL-101"] }
//
// Esta colección es la fuente de verdad para el grafo Neo4j:
// `actualizar_grafo.py` borra/crea relaciones [:PRERREQUIERE]
// para que coincidan exactamente con estos documentos.
// ------------------------------------------------------------
print("\n== reglas_negocio ==");
asegurarColeccion("reglas_negocio", {
  bsonType: "object",
  required: ["materia", "prerrequisitos"],
  properties: {
    materia: {
      bsonType: "string",
      description: "Código de la materia (ej. 'CAL-201')",
    },
    prerrequisitos: {
      bsonType: "array",
      description:
        "Códigos de los prerrequisitos (vacío = sin prerrequisitos)",
      items: { bsonType: "string" },
    },
  },
});

// Índice único sobre `materia`: una única regla por materia evita
// estados ambiguos. (Si necesitaras versiones/historial de reglas,
// quita la opción `unique` y usa { materia: 1, version: 1 }.)
db.reglas_negocio.createIndex(
  { materia: 1 },
  { unique: true, name: "uq_reglas_materia" }
);
print("  Índice único 'uq_reglas_materia' sobre {materia:1} OK.");

// ------------------------------------------------------------
// Ejemplo (comentado) de cómo el Administrador insertaría una regla:
//   db.reglas_negocio.insertOne({
//     materia: "CAL-201",
//     prerrequisitos: ["CAL-101"]
//   });
// Tras cada cambio en `reglas_negocio`, ejecutar:
//   python actualizar_grafo.py
// ------------------------------------------------------------

// ------------------------------------------------------------
// Resumen
// ------------------------------------------------------------
print("\nEsquema MongoDB listo. Colecciones creadas (vacías):");
print("  - catalogo_materias : " + db.catalogo_materias.countDocuments() + " documentos");
print("  - reglas_negocio    : " + db.reglas_negocio.countDocuments() + " documentos");
print("\nSiguientes pasos:");
print("  1. El Data Lake llenará 'catalogo_materias' desde CSV.");
print("  2. El Admin/API insertará reglas en 'reglas_negocio'.");
print("  3. Ejecutar `python actualizar_grafo.py` para sincronizar Neo4j.");
