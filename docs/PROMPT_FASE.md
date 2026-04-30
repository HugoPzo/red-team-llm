# Prompt para documentar una fase completada

Copia y pega este prompt cada vez que completes una fase del proyecto.
Reemplaza los valores entre corchetes con los datos reales de la sesion.

---

```
Acabo de completar la [NOMBRE_FASE] del proyecto (ej: "F2 — V1 Direct Injection").

Lee los siguientes archivos para tener contexto completo:
- PROJECT_BRIEF.md
- PROGRESS.md
- docs/documento_academico.md

Luego haz las siguientes acciones:

1. DOCUMENTO ACADEMICO (docs/documento_academico.md):
   Agrega una nueva subseccion dentro de "3. Metodologia" para esta fase.
   La subseccion debe seguir el mismo formato y nivel de detalle que las
   secciones 3.7 (F0) y 3.8 (F1) ya escritas. Debe incluir:
   - Objetivo de la fase
   - Descripcion tecnica de lo implementado (con fragmentos de codigo relevantes)
   - Decisiones de diseño tomadas y su justificacion
   - Tabla de resumen de evidencia al final

   Si la fase genera resultados comparativos (F5, F6), llena tambien la
   seccion "5. Resultados" con los datos obtenidos.

   Si es la ultima fase (F7), completa tambien "6. Conclusiones".

2. PROGRESS.md:
   Marca la fase como completada y actualiza el estado actual.

Antes de escribir cualquier cosa, muestra un resumen de lo que vas a
agregar y espera mi confirmacion.
```

---

## Notas de uso

- Usar despues de cada fase: F2, F3, F4, F5, F6, F7.
- F4 (persistencia) y F7 (dashboard) pueden no tener subseccion de Metodologia
  propias si su contenido ya quedo cubierto en fases anteriores; en ese caso
  solo actualizar Resultados o Laboratorio con las capturas de evidencia.
- Para F8 (documento academico final), el trabajo sera revisar todo el documento,
  verificar coherencia, agregar capturas como figuras y exportar a PDF.
