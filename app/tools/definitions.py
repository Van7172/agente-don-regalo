"""
Esquemas de herramientas en formato OpenAI function calling.
Separados de la lógica de ejecución para que sean fáciles de leer y modificar.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "listar_categorias",
            "description": "Lista todas las categorías y subcategorías de la tienda. Úsala cuando el cliente quiera ver qué productos hay disponibles o pida ver el catálogo.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_ocasiones",
            "description": "Lista todas las ocasiones disponibles: Cumpleaños, Aniversario, Nacimiento, etc. Úsala antes de buscar productos por ocasión.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_semantico",
            "description": (
                "BÚSQUEDA PRINCIPAL de productos. Úsala SIEMPRE que el cliente describa "
                "lo que busca con palabras (intención, estilo, sentimiento, ocasión, "
                "tipo de producto), ej: 'algo romántico para mi novia', 'un detalle "
                "para felicitar a mi jefe', 'rosas blancas elegantes', 'desayuno "
                "sorpresa'. Entiende el significado, no solo palabras exactas, así que "
                "evita confundir 'rosas blancas' de cumpleaños con arreglos fúnebres. "
                "Por defecto NO devuelve productos fúnebres (pon incluir_funebre=true "
                "solo si el cliente pide explícitamente un arreglo de condolencia/fúnebre)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {
                        "type": "string",
                        "description": "Lo que el cliente busca, descrito de la forma más rica posible (incluye estilo, ocasión y características que mencionó).",
                    },
                    "id_ocasion": {
                        "type": "integer",
                        "description": "Opcional. Filtra por ocasión si el cliente la indicó: Cumpleaños=1, Aniversario=2, Felicitación=3, Nacimiento=4, Agradecimiento=5, Negocios=6, Otros=7.",
                    },
                    "precio_max": {
                        "type": "number",
                        "description": "Opcional. Precio máximo en USD si el cliente dio un presupuesto.",
                    },
                    "incluir_funebre": {
                        "type": "boolean",
                        "description": "Por defecto false. Ponlo en true SOLO si el cliente pide explícitamente un arreglo fúnebre o de condolencias.",
                    },
                    "preferencias": {
                        "type": "string",
                        "description": "Opcional. Gustos DURABLES del cliente que conoces de su historial (DATOS CONOCIDOS), ej: 'le gustan los girasoles y los colores pastel, prefiere detalles con chocolate'. Se usan para personalizar el ranking sin sobreescribir lo que pide ahora. No inventes: solo lo que realmente sabes del cliente.",
                    },
                    "categoria_slug": {
                        "type": "string",
                        "description": "Opcional. Restringe la búsqueda a una categoría exacta. Úsalo cuando el cliente mencionó explícitamente una categoría, y OBLIGATORIAMENTE cuando busques dentro de una campaña de temporada (día del padre, navidad, etc.). Slugs permanentes: desayunos, arreglos-florales, peluches, plantas, cestas, regalo-para-bebe, arreglos-funebres. Slugs de campaña (rotan, confírmalos con listar_categorias): dia-del-padre, dia-de-la-madre, etc.",
                    },
                },
                "required": ["q"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "productos_similares",
            "description": (
                "Devuelve productos PARECIDOS a uno que el cliente ya vio y le gustó. "
                "Úsala cuando el cliente diga cosas como 'muéstrame algo similar', "
                "'¿tienes otros parecidos?', 'algo así pero diferente', o cuando quieras "
                "ofrecer alternativas a un producto que acaba de ver. Pasa el id_producto "
                "de ese producto. Por defecto NO incluye fúnebres."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id_producto": {
                        "type": "integer",
                        "description": "id_producto del producto de referencia (el que le gustó al cliente).",
                    },
                    "incluir_funebre": {
                        "type": "boolean",
                        "description": "Por defecto false. true solo en contexto fúnebre explícito.",
                    },
                },
                "required": ["id_producto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_productos",
            "description": (
                "Búsqueda por coincidencia de texto exacta (nombre o característica). "
                "Úsala como respaldo cuando buscar_semantico no encuentre lo que el "
                "cliente menciona, o cuando el cliente dé un nombre/término muy puntual. "
                "Si conoces la ocasión, pasa `id_ocasion`. "
                "Por defecto NO devuelve arreglos fúnebres."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {
                        "type": "string",
                        "description": "Término de búsqueda del producto que el cliente mencionó (ej: rosas, peluche, desayuno)",
                    },
                    "id_ocasion": {
                        "type": "integer",
                        "description": "Opcional. Filtra por ocasión: Cumpleaños=1, Aniversario=2, Felicitación=3, Nacimiento=4, Agradecimiento=5, Negocios=6, Otros=7. Úsalo si el cliente indicó la ocasión.",
                    },
                    "orden": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "description": "Orden por precio: asc (menor a mayor) o desc (mayor a menor). Por defecto asc.",
                    },
                },
                "required": ["q"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "catalogo_categoria",
            "description": "Obtiene los productos de una categoría específica. Usa el slug (url_categoria) obtenido de listar_categorias. Ejemplos de slugs: arreglos-florales, desayunos, peluches, plantas, cestas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "El slug (url_categoria) de la categoría, ej: arreglos-florales, desayunos, peluches",
                    },
                },
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "productos_destacados",
            "description": "Obtiene los productos más populares y destacados. Úsala cuando el cliente no sepa qué elegir, pida recomendaciones o pregunte qué es lo más vendido.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "productos_oferta",
            "description": "Obtiene productos con descuento o en oferta. Úsala cuando el cliente busque algo económico, pregunte por promociones, descuentos o las mejores ofertas.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detalle_producto",
            "description": "Obtiene el detalle completo de un producto: descripción, precio, imágenes y relacionados. Úsala cuando el cliente quiera saber más de un producto que ya apareció en una búsqueda.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id_producto": {
                        "type": "integer",
                        "description": "El id_producto numérico obtenido de buscar_productos, catalogo_categoria o productos_destacados",
                    },
                },
                "required": ["id_producto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "productos_por_ocasion",
            "description": "Obtiene productos sugeridos para una ocasión. IDs: Cumpleaños=1, Aniversario=2, Felicitación=3, Nacimiento=4, Agradecimiento=5, Negocios=6, Otros=7.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id_ocasion": {
                        "type": "integer",
                        "description": "El id de la ocasión. Cumpleaños=1, Aniversario=2, Felicitación=3, Nacimiento=4, Agradecimiento=5, Negocios=6, Otros=7",
                    },
                },
                "required": ["id_ocasion"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "distritos_cobertura",
            "description": "Lista los distritos de Lima con cobertura de delivery y tarifa de envío. Úsala cuando el cliente pregunte si llegan a su zona o cuánto cuesta el envío.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "metodos_pago",
            "description": "Lista los métodos de pago disponibles. Úsala cuando el cliente pregunte cómo puede pagar su pedido.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tipo_cambio",
            "description": "Obtiene el tipo de cambio actual USD→Soles. Úsala para convertir precios de productos (que vienen en USD) a Soles antes de mostrarlos.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rastrear_pedido",
            "description": "Rastrea el estado de un pedido. SIEMPRE pide al cliente su email y código de pedido antes de usar esta herramienta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email":  {"type": "string", "description": "email del cliente"},
                    "codigo": {"type": "string", "description": "código del pedido"},
                },
                "required": ["email", "codigo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_conocimiento_equipo",
            "description": (
                "Consulta la base de conocimiento aprendida de los vendedores humanos. "
                "Úsala cuando el cliente haga una pregunta que NO se resuelve con las otras "
                "herramientas: dudas de políticas, casos especiales, objeciones (precio, "
                "tiempos, desconfianza), coordinaciones o situaciones poco comunes. "
                "Si encuentra una respuesta del equipo con buen puntaje, úsala como guía "
                "para responder con el tono y los datos que ya funcionaron. Si no devuelve "
                "nada útil, responde con tu criterio o deriva al equipo humano."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {
                        "type": "string",
                        "description": "La duda o situación del cliente, redactada de forma clara.",
                    },
                },
                "required": ["q"],
            },
        },
    },
]

MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "guardar_datos_cliente",
        "description": (
            "Guarda datos del cliente para recordarlos en futuras conversaciones. "
            "Usa `nombre` y `distrito` para datos ESTABLES (se sobrescriben con el valor "
            "actual). Usa `nota` para AÑADIR un recuerdo episódico al historial (compras, "
            "ocasiones, preferencias puntuales) — cada nota se acumula con fecha, no se pierde. "
            "Solo envía los campos que conozcas."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "nombre":   {"type": "string", "description": "Nombre del cliente (dato estable)"},
                "distrito": {"type": "string", "description": "Distrito de entrega habitual en Lima (dato estable)"},
                "nota": {
                    "type": "string",
                    "description": (
                        "Un recuerdo episódico para AÑADIR al historial. Una sola frase concreta. "
                        "Ej: 'Compró un desayuno para el cumpleaños de su mamá', "
                        "'Le interesan arreglos con rosas blancas para nacimiento', "
                        "'Prefiere productos económicos'."
                    ),
                },
            },
            "required": [],
        },
    },
}
