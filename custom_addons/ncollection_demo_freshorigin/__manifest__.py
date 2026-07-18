{
    'name': 'NCollection Demo: Fresh Origin',
    'version': '19.0.1.0.0',
    'category': 'Demo',
    'summary': 'Realistic demo data: Fresh Origin showcase organization',
    'description': """
Populates CRM, Sales, Inventory, Accounting, HR, Project and Purchase
with realistic, internally-consistent data for the Fresh Origin demo
account used during client presentations.
""",
    'author': 'NCollection',
    'license': 'LGPL-3',
    'depends': [
        'crm',
        'sale_management',
        'stock',
        'purchase',
        'account',
        'hr',
        'project',
    ],
    'data': [
        'data/01_partners.xml',
        'data/02_products.xml',
        'data/03_hr.xml',
        'data/04_project.xml',
        'data/05_crm.xml',
        'data/06_sales.xml',
        'data/07_purchase.xml',
        'data/08_inventory.xml',
        'data/09_accounting.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'post_init_hook': '_post_init_hook',
}
