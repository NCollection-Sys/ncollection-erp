# -*- coding: utf-8 -*-
"""P8-T02: Dynamic OpenAPI 3.1 schema specification endpoint (/api/v1/openapi.json)."""
from odoo import http

from .common import API_ROOT, ApiControllerBase, _json


class OpenApiSpecificationController(ApiControllerBase):

    @http.route('%s/openapi.json' % API_ROOT, type='http', auth='none',
                methods=['GET'], csrf=False, save_session=False,
                readonly=False)
    def openapi_spec(self, **kwargs):
        """Returns the dynamic OpenAPI 3.1 schema for NCollection REST API."""
        spec = {
            "openapi": "3.1.0",
            "info": {
                "title": "NCollection ERP Public REST API",
                "version": "1.0.0",
                "description": (
                    "Enterprise Multi-Tenant REST API for NCollection ERP. "
                    "Secured with OAuth2 Client Credentials & Scoped Bearer Tokens."
                ),
                "contact": {
                    "name": "NCollection API Support",
                    "url": "https://ncollection.com"
                }
            },
            "servers": [
                {"url": "/api/v1", "description": "API v1 root"}
            ],
            "paths": {
                "/oauth/token": {
                    "post": {
                        "summary": "Request OAuth2 Bearer Token",
                        "description": (
                            "Exchange client_id and client_secret for a scoped bearer access token."
                        ),
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/x-www-form-urlencoded": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["grant_type", "client_id", "client_secret", "scope"],
                                        "properties": {
                                            "grant_type": {
                                                "type": "string",
                                                "enum": ["client_credentials"]
                                            },
                                            "client_id": {"type": "string"},
                                            "client_secret": {"type": "string"},
                                            "scope": {
                                                "type": "string",
                                                "example": "contacts:read sales:read"
                                            }
                                        }
                                    }
                                }
                            }
                        },
                        "responses": {
                            "200": {"description": "Token issued successfully"},
                            "400": {"$ref": "#/components/responses/ErrorResponse"},
                            "401": {"$ref": "#/components/responses/ErrorResponse"},
                            "429": {"$ref": "#/components/responses/ErrorResponse"}
                        }
                    }
                },
                "/contacts": {
                    "get": {
                        "summary": "List contacts",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 50}},
                            {"name": "offset", "in": "query", "schema": {"type": "integer", "default": 0}},
                            {"name": "name", "in": "query", "schema": {"type": "string"}},
                            {"name": "email", "in": "query", "schema": {"type": "string"}},
                            {"name": "is_company", "in": "query", "schema": {"type": "boolean"}}
                        ],
                        "responses": {
                            "200": {"description": "Contacts list"},
                            "401": {"$ref": "#/components/responses/ErrorResponse"},
                            "403": {"$ref": "#/components/responses/ErrorResponse"}
                        }
                    },
                    "post": {
                        "summary": "Create contact",
                        "security": [{"bearerAuth": []}],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ContactInput"}
                                }
                            }
                        },
                        "responses": {
                            "201": {"description": "Contact created"},
                            "400": {"$ref": "#/components/responses/ErrorResponse"}
                        }
                    }
                },
                "/contacts/{id}": {
                    "get": {
                        "summary": "Get contact by ID",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                        ],
                        "responses": {
                            "200": {"description": "Contact details"},
                            "404": {"$ref": "#/components/responses/ErrorResponse"}
                        }
                    },
                    "put": {
                        "summary": "Update contact",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                        ],
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ContactInput"}
                                }
                            }
                        },
                        "responses": {"200": {"description": "Contact updated"}}
                    },
                    "delete": {
                        "summary": "Archive contact",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                        ],
                        "responses": {"200": {"description": "Contact archived"}}
                    }
                },
                "/products": {
                    "get": {
                        "summary": "List products",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                            {"name": "offset", "in": "query", "schema": {"type": "integer"}},
                            {"name": "name", "in": "query", "schema": {"type": "string"}},
                            {"name": "default_code", "in": "query", "schema": {"type": "string"}}
                        ],
                        "responses": {"200": {"description": "Products list"}}
                    },
                    "post": {
                        "summary": "Create product",
                        "security": [{"bearerAuth": []}],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ProductInput"}
                                }
                            }
                        },
                        "responses": {"201": {"description": "Product created"}}
                    }
                },
                "/products/{id}": {
                    "get": {
                        "summary": "Get product by ID",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                        ],
                        "responses": {"200": {"description": "Product details"}}
                    },
                    "put": {
                        "summary": "Update product",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                        ],
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ProductInput"}
                                }
                            }
                        },
                        "responses": {"200": {"description": "Product updated"}}
                    }
                },
                "/sales": {
                    "get": {
                        "summary": "List sales orders",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                            {"name": "offset", "in": "query", "schema": {"type": "integer"}},
                            {"name": "partner_id", "in": "query", "schema": {"type": "integer"}},
                            {"name": "state", "in": "query", "schema": {"type": "string"}}
                        ],
                        "responses": {"200": {"description": "Sales orders list"}}
                    },
                    "post": {
                        "summary": "Create sales order",
                        "security": [{"bearerAuth": []}],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/SaleOrderInput"}
                                }
                            }
                        },
                        "responses": {"201": {"description": "Sales order created"}}
                    }
                },
                "/sales/{id}": {
                    "get": {
                        "summary": "Get sales order by ID",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                        ],
                        "responses": {"200": {"description": "Sales order details"}}
                    },
                    "put": {
                        "summary": "Update sales order",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                        ],
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            }
                        },
                        "responses": {"200": {"description": "Sales order updated"}}
                    }
                },
                "/sales/{id}/action_confirm": {
                    "post": {
                        "summary": "Confirm sales order",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                        ],
                        "responses": {"200": {"description": "Order confirmed"}}
                    }
                },
                "/invoices": {
                    "get": {
                        "summary": "List customer invoices",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                            {"name": "offset", "in": "query", "schema": {"type": "integer"}},
                            {"name": "partner_id", "in": "query", "schema": {"type": "integer"}},
                            {"name": "state", "in": "query", "schema": {"type": "string"}}
                        ],
                        "responses": {"200": {"description": "Invoices list"}}
                    },
                    "post": {
                        "summary": "Create customer invoice",
                        "security": [{"bearerAuth": []}],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/InvoiceInput"}
                                }
                            }
                        },
                        "responses": {"201": {"description": "Invoice created"}}
                    }
                },
                "/invoices/{id}": {
                    "get": {
                        "summary": "Get invoice by ID",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                        ],
                        "responses": {"200": {"description": "Invoice details"}}
                    }
                },
                "/invoices/{id}/action_post": {
                    "post": {
                        "summary": "Post draft invoice",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                        ],
                        "responses": {"200": {"description": "Invoice posted"}}
                    }
                },
                "/stock/levels": {
                    "get": {
                        "summary": "Query stock levels",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "product_id", "in": "query", "schema": {"type": "integer"}},
                            {"name": "location_id", "in": "query", "schema": {"type": "integer"}},
                            {"name": "limit", "in": "query", "schema": {"type": "integer"}}
                        ],
                        "responses": {"200": {"description": "Stock levels list"}}
                    }
                },
                "/crm/leads": {
                    "get": {
                        "summary": "List CRM leads",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "stage_id", "in": "query", "schema": {"type": "integer"}},
                            {"name": "limit", "in": "query", "schema": {"type": "integer"}}
                        ],
                        "responses": {"200": {"description": "Leads list"}}
                    },
                    "post": {
                        "summary": "Create CRM lead",
                        "security": [{"bearerAuth": []}],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/CrmLeadInput"}
                                }
                            }
                        },
                        "responses": {"201": {"description": "Lead created"}}
                    }
                },
                "/crm/leads/{id}": {
                    "get": {
                        "summary": "Get CRM lead by ID",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                        ],
                        "responses": {"200": {"description": "Lead details"}}
                    },
                    "put": {
                        "summary": "Update CRM lead",
                        "security": [{"bearerAuth": []}],
                        "parameters": [
                            {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                        ],
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            }
                        },
                        "responses": {"200": {"description": "Lead updated"}}
                    }
                },
                "/webhooks/subscriptions": {
                    "get": {
                        "summary": "List Webhook Subscriptions",
                        "security": [{"bearerAuth": []}],
                        "responses": {"200": {"description": "List of subscriptions"}}
                    },
                    "post": {
                        "summary": "Create Webhook Subscription",
                        "security": [{"bearerAuth": []}],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/WebhookSubscriptionInput"}
                                }
                            }
                        },
                        "responses": {"201": {"description": "Subscription created"}}
                    }
                },
                "/webhooks/deliveries": {
                    "get": {
                        "summary": "Query Webhook Delivery Logs",
                        "security": [{"bearerAuth": []}],
                        "responses": {"200": {"description": "List of deliveries"}}
                    }
                }
            },
            "components": {
                "securitySchemes": {
                    "bearerAuth": {
                        "type": "http",
                        "scheme": "bearer",
                        "bearerFormat": "Scoped Token"
                    }
                },
                "schemas": {
                    "WebhookSubscriptionInput": {
                        "type": "object",
                        "required": ["name", "target_url"],
                        "properties": {
                            "name": {"type": "string"},
                            "target_url": {"type": "string"},
                            "event_types": {"type": "string", "example": "sale.order.confirmed,invoice.posted"},
                            "secret": {"type": "string"}
                        }
                    },
                    "ErrorResponse": {
                        "type": "object",
                        "properties": {
                            "error": {
                                "type": "object",
                                "properties": {
                                    "code": {"type": "string"},
                                    "message": {"type": "string"}
                                },
                                "required": ["code", "message"]
                            }
                        }
                    },
                    "ContactInput": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string"},
                            "email": {"type": "string"},
                            "phone": {"type": "string"},
                            "is_company": {"type": "boolean"},
                            "street": {"type": "string"},
                            "city": {"type": "string"},
                            "vat": {"type": "string"}
                        }
                    },
                    "ProductInput": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string"},
                            "default_code": {"type": "string"},
                            "list_price": {"type": "number"},
                            "type": {"type": "string", "enum": ["consu", "service"]}
                        }
                    },
                    "SaleOrderInput": {
                        "type": "object",
                        "required": ["partner_id"],
                        "properties": {
                            "partner_id": {"type": "integer"},
                            "order_line": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["product_id"],
                                    "properties": {
                                        "product_id": {"type": "integer"},
                                        "product_uom_qty": {"type": "number"},
                                        "price_unit": {"type": "number"}
                                    }
                                }
                            }
                        }
                    },
                    "InvoiceInput": {
                        "type": "object",
                        "required": ["partner_id"],
                        "properties": {
                            "partner_id": {"type": "integer"},
                            "invoice_date": {"type": "string", "format": "date"},
                            "invoice_line_ids": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "product_id": {"type": "integer"},
                                        "quantity": {"type": "number"},
                                        "price_unit": {"type": "number"},
                                        "name": {"type": "string"}
                                    }
                                }
                            }
                        }
                    },
                    "CrmLeadInput": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string"},
                            "partner_id": {"type": "integer"},
                            "expected_revenue": {"type": "number"},
                            "email_from": {"type": "string"},
                            "phone": {"type": "string"},
                            "description": {"type": "string"}
                        }
                    }
                },
                "responses": {
                    "ErrorResponse": {
                        "description": "Standard error response",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                            }
                        }
                    }
                }
            }
        }
        return _json(spec)
