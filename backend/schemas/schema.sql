--
-- PostgreSQL database dump
--

\restrict Vn3Tma4R5cSORuitMQRwDnwpybkqB5Ggzd10I7TMikC4IPgl3UffNENslgyi8k9

-- Dumped from database version 17.6 (Ubuntu 17.6-1.pgdg22.04+1)
-- Dumped by pg_dump version 17.6 (Ubuntu 17.6-1.pgdg22.04+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: touch_images_updated(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.touch_images_updated() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.date_updated := now();
  RETURN NEW;
END;
$$;


--
-- Name: touch_invoice_items_updated(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.touch_invoice_items_updated() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.date_updated := now();
  RETURN NEW;
END;
$$;


--
-- Name: touch_item_images_updated(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.touch_item_images_updated() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.date_updated := now();
  RETURN NEW;
END;
$$;


--
-- Name: touch_relationship_updated(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.touch_relationship_updated() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.date_updated := now();
  RETURN NEW;
END;
$$;


--
-- Name: update_date_last_modified(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_date_last_modified() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.date_last_modified = now();
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;


--
-- Name: images; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.images (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    dir text DEFAULT 'imgs'::text NOT NULL,
    file_name text DEFAULT ''::text NOT NULL,
    source_url text DEFAULT ''::text NOT NULL,
    has_renamed boolean DEFAULT true NOT NULL,
    original_file_name text DEFAULT ''::text NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    overlay_svg text DEFAULT ''::text NOT NULL,
    dim_width integer DEFAULT 0 NOT NULL,
    dim_height integer DEFAULT 0 NOT NULL,
    is_pano boolean DEFAULT false NOT NULL,
    is_360 boolean DEFAULT false NOT NULL,
    date_updated timestamp with time zone DEFAULT now() NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    CONSTRAINT images_dim_height_check CHECK ((dim_height >= 0)),
    CONSTRAINT images_dim_width_check CHECK ((dim_width >= 0))
);


--
-- Name: invoice_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoice_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    item_id uuid NOT NULL,
    invoice_id uuid NOT NULL,
    date_updated timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: invoices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoices (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    date timestamp with time zone DEFAULT now() NOT NULL,
    order_number text DEFAULT ''::text NOT NULL,
    shop_name text DEFAULT ''::text NOT NULL,
    urls text DEFAULT ''::text NOT NULL,
    subject text DEFAULT ''::text NOT NULL,
    html text DEFAULT ''::text NOT NULL,
    auto_summary text DEFAULT ''::text,
    notes text DEFAULT ''::text NOT NULL,
    has_been_processed boolean DEFAULT false NOT NULL,
    snooze timestamp with time zone DEFAULT now() NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    pin_as_opened timestamp with time zone DEFAULT NULL
);


--
-- Name: item_images; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.item_images (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    item_id uuid NOT NULL,
    img_id uuid NOT NULL,
    rank integer DEFAULT 0 NOT NULL,
    date_updated timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    short_id integer DEFAULT 0,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    remarks text DEFAULT ''::text NOT NULL,
    quantity text DEFAULT ''::text NOT NULL,
    date_creation timestamp with time zone DEFAULT now() NOT NULL,
    date_last_modified timestamp with time zone DEFAULT now() NOT NULL,
    is_container boolean DEFAULT false NOT NULL,
    is_collection boolean DEFAULT false NOT NULL,
    is_large boolean DEFAULT false NOT NULL,
    is_small boolean DEFAULT false NOT NULL,
    is_fixed_location boolean DEFAULT false NOT NULL,
    is_tree_root boolean DEFAULT false NOT NULL,
    is_consumable boolean DEFAULT false NOT NULL,
    metatext text DEFAULT ''::text NOT NULL,
    textsearch tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, ((((COALESCE(name, ''::text) || ' '::text) || COALESCE(description, ''::text)) || ' '::text) || COALESCE(metatext, ''::text)))) STORED,
    is_staging boolean DEFAULT true NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    is_lost boolean DEFAULT false NOT NULL,
    date_reminder timestamp with time zone,
    product_code text DEFAULT ''::text NOT NULL,
    url text DEFAULT ''::text NOT NULL,
    date_purchased timestamp with time zone,
    source text DEFAULT ''::text NOT NULL,
    pin_as_opened timestamp with time zone DEFAULT NULL
);


--
-- Name: relationships; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.relationships (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    item_id uuid NOT NULL,
    assoc_id uuid NOT NULL,
    assoc_type smallint NOT NULL DEFAULT 0,
    notes text DEFAULT ''::text NOT NULL,
    date_updated timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT relationships_check CHECK ((item_id <> assoc_id))
);


--
-- Name: test_table; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.test_table (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text DEFAULT ''::text NOT NULL,
    is_active boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    short_id integer DEFAULT 0 NOT NULL,
    embedding public.vector(4) NOT NULL
);


--
-- Name: gmail_seen; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.gmail_seen (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),  -- random PK
    email_uuid   bytea NOT NULL,                              -- the email's own 64 bit UID
    date_seen    timestamptz NOT NULL DEFAULT now(),          -- when processed
    invoice_id   uuid UNIQUE,                                 -- one-to-one to invoices.id (nullable)
    CONSTRAINT gmail_seen_email_uuid_uniq UNIQUE (email_uuid),
    CONSTRAINT gmail_seen_invoice_fk
        FOREIGN KEY (invoice_id)
        REFERENCES public.invoices (id)
        ON DELETE SET NULL       -- relationship can be null; do not cascade delete
);


CREATE TABLE public.imail_seen (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),  -- random PK
    email_uuid   bytea NOT NULL,                              -- the email's own 64 bit UID
    date_seen    timestamptz NOT NULL DEFAULT now(),          -- when processed
    invoice_id   uuid UNIQUE,                                 -- one-to-one to invoices.id (nullable)
    CONSTRAINT imail_seen_email_uuid_uniq UNIQUE (email_uuid),
    CONSTRAINT imail_seen_invoice_fk
        FOREIGN KEY (invoice_id)
        REFERENCES public.invoices (id)
        ON DELETE SET NULL       -- relationship can be null; do not cascade delete
);


CREATE TABLE history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- timestamp when event was logged
    date TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- who did it
    username TEXT NOT NULL DEFAULT '',

    -- optional references to items
    item_id_1 UUID NULL REFERENCES items(id) ON DELETE SET NULL,
    item_id_2 UUID NULL REFERENCES items(id) ON DELETE SET NULL,

    -- event type/description
    event TEXT NOT NULL DEFAULT '',

    -- additional details as JSON/text
    meta TEXT NOT NULL DEFAULT ''
);


--
-- Name: images images_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.images
    ADD CONSTRAINT images_pkey PRIMARY KEY (id);


--
-- Name: invoice_items invoice_items_item_id_invoice_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT invoice_items_item_id_invoice_id_key UNIQUE (item_id, invoice_id);


--
-- Name: invoice_items invoice_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT invoice_items_pkey PRIMARY KEY (id);


--
-- Name: invoices invoices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_pkey PRIMARY KEY (id);


--
-- Name: item_images item_images_item_id_img_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_images
    ADD CONSTRAINT item_images_item_id_img_id_key UNIQUE (item_id, img_id);


--
-- Name: item_images item_images_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_images
    ADD CONSTRAINT item_images_pkey PRIMARY KEY (id);


--
-- Name: items items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.items
    ADD CONSTRAINT items_pkey PRIMARY KEY (id);


--
-- Name: relationships relationships_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.relationships
    ADD CONSTRAINT relationships_pkey PRIMARY KEY (id);


--
-- Name: test_table test_table_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_table
    ADD CONSTRAINT test_table_pkey PRIMARY KEY (id);


--
-- Name: images_dir_file_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX images_dir_file_idx ON public.images USING btree (dir, file_name);


--
-- Name: invoice_items_invoice_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX invoice_items_invoice_idx ON public.invoice_items USING btree (invoice_id);


--
-- Name: invoice_items_item_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX invoice_items_item_idx ON public.invoice_items USING btree (item_id);


--
-- Name: item_images_img_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX item_images_img_idx ON public.item_images USING btree (img_id);


--
-- Name: item_images_item_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX item_images_item_idx ON public.item_images USING btree (item_id);


--
-- Name: items_short_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX items_short_id_idx ON public.items USING btree (short_id);


--
-- Name: items_textsearch_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX items_textsearch_idx ON public.items USING gin (textsearch);


--
-- Name: relationships_assoc_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX relationships_assoc_idx ON public.relationships USING btree (assoc_id);


--
-- Name: relationships_item_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX relationships_item_idx ON public.relationships USING btree (item_id);


--
-- Name: relationships_type_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX relationships_type_idx ON public.relationships USING btree (assoc_type);


--
-- Name: items set_date_last_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER set_date_last_modified BEFORE UPDATE ON public.items FOR EACH ROW EXECUTE FUNCTION public.update_date_last_modified();


--
-- Name: images trg_touch_images_updated; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_touch_images_updated BEFORE UPDATE ON public.images FOR EACH ROW EXECUTE FUNCTION public.touch_images_updated();


--
-- Name: invoice_items trg_touch_invoice_items_updated; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_touch_invoice_items_updated BEFORE UPDATE ON public.invoice_items FOR EACH ROW EXECUTE FUNCTION public.touch_invoice_items_updated();


--
-- Name: item_images trg_touch_item_images_updated; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_touch_item_images_updated BEFORE UPDATE ON public.item_images FOR EACH ROW EXECUTE FUNCTION public.touch_item_images_updated();


--
-- Name: relationships trg_touch_relationship_updated; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_touch_relationship_updated BEFORE UPDATE ON public.relationships FOR EACH ROW EXECUTE FUNCTION public.touch_relationship_updated();


--
-- Name: invoice_items invoice_items_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT invoice_items_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.invoices(id) ON DELETE CASCADE;


--
-- Name: invoice_items invoice_items_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT invoice_items_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.items(id) ON DELETE CASCADE;


--
-- Name: item_images item_images_img_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_images
    ADD CONSTRAINT item_images_img_id_fkey FOREIGN KEY (img_id) REFERENCES public.images(id) ON DELETE CASCADE;


--
-- Name: item_images item_images_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_images
    ADD CONSTRAINT item_images_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.items(id) ON DELETE CASCADE;


--
-- Name: relationships relationships_assoc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.relationships
    ADD CONSTRAINT relationships_assoc_id_fkey FOREIGN KEY (assoc_id) REFERENCES public.items(id) ON DELETE CASCADE;


--
-- Name: relationships relationships_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.relationships
    ADD CONSTRAINT relationships_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.items(id) ON DELETE CASCADE;
