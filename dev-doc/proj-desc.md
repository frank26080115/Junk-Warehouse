This project is a DIY home inventory database. Items can be both items and containers, and their relations helps me find where I've stored each item. The frontend uses Node.js + React + Bootstrap, and the backend uses Python + Flask + PostgreSQL + SQLalchemy. The project file tree currently looks like

```
├───backend
│   ├───app
│   ├───automation
│   ├───email
│   ├───schemas
│   ├───services
│   ├───shop_handler
│   └───tools
├───config
├───dev-doc
├───frontend
│   ├───public
│   │   ├───imgs
│   │   │   └───icons
│   │   ├───scripts
│   │   └───styles
│   └───src
│       ├───app
│       │   ├───components
│       │   └───helpers
│       ├───pages
│       └───styles
└───scripts
```

There is an installation specific `/config/appconfig.json` that stores things like timezone

For the routing, the files `/frontend/index.html` and `/frontend/src/main.tsx` and `/frontend/src/app/App.tsx` are responsible for routing

The Python Flask server will offer some API calls, most are using the url similar to `/api/functionname`

The database has three main tables

 * `items` for inventory items
 * `invoices` for invoices
 * `images` for images

There are other relationship tables:

 * `relationships` for item-to-item relationships
 * `item_images` for attaching images to inventory items
 * `invoice_items` for attaching invoices to inventory items

There are some tables for embeddings to aid in search:

 * `item_embeddings`
 * `container_embeddings`

Inventory item relationships can have types:

 * containment
 * consumable / accessory / power
 * similar / alternative
 * merge (special flag to trigger merging items)

There is no direction for the item-to-item relationship, and a maintainance task will try to ensure a direction if needed (such as big containers contain smaller containers)

The database schema is shown in `backend\schemas\schema.sql`
