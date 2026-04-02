# 🛡️ API Gateway:

### Microservicio de Integración de Identidad y Seguridad Social

Este proyecto es un Gateway desarrollado en **FastAPI**
que centraliza y normaliza las consultas a servicios de identidad y seguridad social (RENAPO, IMSS, SAT, ISSSTE).

## 🚀 Características Principales:
* **Arquitectura Híbrida:** Soporte para respuestas síncronas y flujos asíncronas mediante **Webhooks/Callbacks**.
* **Normalización de Datos:** Capa de validación con **Pydantic** para asegurar la integridad de los payloads.
* **Seguridad:** Implementación de tokens de validación para webhooks y gestión de variables de entorno.
* **Automatización Compleja:** Integración de **Selenium** para la extracción de datos en portales sin API oficial (Semanas Cotizadas).

## 🛠️ Stack Tecnológico:
* **Lenguaje:** Python 3.10+
* **Framework:** FastAPI / Uvicorn
* **Cliente HTTP:** HTTPX (Async)
* **Automatización:** Selenium WebDriver
* **Validación:** Pydantic v2
