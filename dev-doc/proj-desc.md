This project is a DIY home inventory database. Items can be both items and containers, and their relations helps me find where I've stored each item. The frontend uses Node.js + React + Bootstrap, and the backend uses Python + Flask + PostgreSQL + SQLalchemy. The project file tree currently looks like

```
├───backend
│   ├───app
│   ├───schemas
│   ├───services
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
│       │   └───components
│       ├───pages
│       └───styles
└───scripts
```

There is an installation specific `/config/appconfig.json` that stores things like timezone

For the routing, the files `/frontend/index.html` and `/frontend/src/main.tsx` and `/frontend/src/app/App.tsx` are responsible for routing

| Route | Purpose |
|-------|---------|
| /                     | Home page with a list of available common tasks |
| /search               | Search page (for inventory items) with blank prefilled query input |
| /search/<xyz>         | Search page (for inventory items) with query input prefilled with <xyz> |
| /item/new             | Item edit page that defaults to a blank template ready for insert into database |
| /item/<xyz>           | Item view/edit page that shows a particular item searched for by <xyz> |
| /invoice/<uuid>       | View or edit an invoice |
| /ledger               | Search page for invoices with blank prefilled query input |
| /ledger/<xyz>         | Search page for invoices with query input prefilled with <xyz> |
| /admin                | Page to launch maintainance tasks from |
| /login                | Page to enter user credientials and login |
| /logout               | Visiting causes immediate logout of user |
| /health               | Hello world test page that uses /api/health |
| /test/<xyz>           | Test page that uses /api/test, passing back the query <xyz> |

The Python Flask server will offer some API calls, most are using the url similar to `/api/functionname`

| API Path | Purpose |
|----------|---------|
| /api/health           | Hello world test of server |
| /api/config           | Retrieves configuration JSON object |
| /api/whoami           | returns ok and username if user is logged in, otherwise return error code saying not authorized |
| /api/login            | User login |
| /api/logout           | User logout |
| /api/getitem          | Retrieves inventory item JSON object |
| /api/setitem          | Writes inventory item JSON object |
| /api/deleteitem       | Delete inventory item |
| /api/getinvoice       | Retrieves invoice JSON object |
| /api/setinvoice       | Writes invoice JSON object |
| /api/uploadinvoice    | Upload invoice file and create database entry for it |
| /api/deleteinvoice    | Deletes an invoice |
| /api/getimage         | Retrieves image JSON object (contains metadata and file path) |
| /api/setimage         | Edit image JSON object (contains metadata and file path) |
| /api/deleteimage      | Delete image object |
| /api/linkitem         | Sets a relationship between inventory items |
| /api/unlinkitem       | Delete a relationship between inventory items |
| /api/linkitem         | Sets a relationship between inventory items |
| /api/unlinkitem       | Delete a relationship between inventory items |
| /api/linkinvoice      | Sets a relationship between inventory item and invoice |
| /api/unlinkinvoice    | Delete a relationship between inventory item and invoice |
| /api/linkimage        | Sets a relationship between inventory item and image |
| /api/unlinkimage      | Delete a relationship between inventory item and image |
| /api/rankimage        | Edits the rank of the item-image relationship, which is the display order |
| /api/search           | Universal database search function, returns list of results |
| /api/searchitems      | Database search function for inventory items, returns list of items |
| /api/searchinvoices   | Database search function for invoices, returns list of invoices |
| /api/searchimages     | Database search function for images, returns list of images |
| /api/task             | General launch point for maintainance tasks |
| /api/test             | General launch point for testing tasks |

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

Inventory item relationships can have types (enum):

 * `containment`
 * `alternative`
 * `consumable`
 * `accessory`
 * `power`
 * `similar`
 * `weak`

There is no direction for the item-to-item relationship, and a maintainance task will try to ensure a direction if needed (such as big containers contain smaller containers)

There are some full pages:

 * Item (inventory item) view/edit
 * Invoice view/edit
 * Search (for inventory items)
 * Ledger (search but for invoices)
 * Home
 * Admin (maintainance, logs, stats)

I am trying to design this so that components are reusable, there are many such panels

 * SearchPanel, can be used as a standalone page for searching inventory items, or as an integrated panel for showing related items.
 * LoginPanel, can be a standalone login page or as a modal login dialog that is called when handling authentication errors
 * LedgerPanel, similar to SearchPanel but for invoices. Can be used as a standalone page for searching invoices, or as an integrated panel for showing related invoices, needs no thumbnail column but needs additional datetime columns
 * ImageGallery, simple list of images related to inventory item, but each image will have 3 buttons under it (move left, delete, move right). Plus an upload button at the end.

Notes:

 * the column named "rank" is sorted as 0 being top, image having rank 0 means it's the main thumbnail
 * the quantity field is freeform text, not a number, I might get lazy and just write things like "enough", or "at least 6"

Configuration Items from `/config/appconfig.json`:

 * timezone
