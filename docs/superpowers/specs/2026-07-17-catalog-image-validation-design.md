# Validación de imágenes del catálogo

## Objetivo

Evitar que Regalito envíe como imagen una URL que en realidad responde con HTML,
un error de PHP o contenido que WhatsApp no puede representar. El cliente debe
recibir una lista compuesta únicamente por productos con una imagen válida.

## Comportamiento

- Antes de componer el listado visible, el agente valida la imagen de cada
  candidato.
- Una respuesta HTTP 200 no basta: la descarga debe tener un tipo de imagen
  admitido y bytes que puedan abrirse como imagen.
- Si la imagen es inválida, se omite el producto completo. No se muestra el
  producto sin foto ni se prueba otra imagen del mismo producto.
- Se continúa recorriendo el conjunto de candidatos hasta completar la cantidad
  solicitada con otros productos válidos.
- Si no existen suficientes candidatos válidos, se muestra una lista más corta;
  nunca se inventan productos o URLs.
- El fallo se registra únicamente en el log interno con el identificador del
  producto y la URL. No se muestra al cliente ningún error técnico ni aviso de
  que se descartó un producto.

## Componentes

### Verificador de imagen

Un componente interno descargará la URL mediante `GET`, seguirá redirecciones y
aplicará límites de tiempo y tamaño. Aceptará el recurso solo si:

1. la respuesta HTTP es exitosa;
2. el tipo de contenido corresponde a una imagen admitida; y
3. la firma y decodificación de los bytes confirman que es una imagen real.

El resultado podrá almacenarse temporalmente por URL para no descargar la misma
imagen varias veces durante conversaciones cercanas.

### Selección y composición

La composición del listado recibirá más candidatos que el máximo que debe
mostrar. Los verificará en orden, conservará el orden original de relevancia y
se detendrá cuando alcance el cupo. Los `artifacts`, los productos recientes y
los IDs mostrados reflejarán solo los productos que realmente llegaron al
cliente.

La descarga usada para validar no convierte al verificador en una fuente de
datos del catálogo: nombres, IDs, precios y URLs continúan procediendo de las
tools y sus adapters.

## Manejo de errores

Errores de red, timeout, redirecciones inválidas, HTML con status 200, contenido
vacío o bytes corruptos se consideran imagen inválida. El turno sigue con el
siguiente candidato. Una caída general del servidor de imágenes no debe tumbar
el turno.

## Pruebas

- HTTP 200 con HTML se rechaza.
- Imagen válida con tipo y bytes correctos se acepta.
- Un candidato inválido se omite y se reemplaza por el siguiente válido.
- Si quedan menos válidos que el cupo, se entrega una lista corta.
- El texto al cliente no contiene mensajes de error ni la URL inválida.
- Los artifacts y productos recientes no incluyen el producto descartado.

## Despliegue

Los cambios se realizan en la raíz y se reflejan exactamente en `sandbox/`.
La validación no cambia el contrato de `API.md`; actúa como defensa ante URLs de
imagen rotas devueltas por el catálogo.
