/* Deciding whether a file is a Faradaem design.
 *
 * Split out of app.js and made pure on purpose. The version this replaced
 * validated two things about the envelope and then mutated the page:
 * switched the circuit, overwrote every input it recognised, cleared the
 * result, redrew, and started a simulation -- and only somewhere in there
 * discovered that a value was the string "oops", or a hundred times the
 * legal maximum, or missing. A rejected file left the reader holding a
 * circuit they did not choose, half filled with a stranger's numbers.
 *
 * So: everything is checked before anything moves, and the checking knows
 * nothing about the DOM. It is handed the catalogue and the version it
 * should compare against, and it returns a verdict. That makes it a
 * function a test can call ten thousand times with no browser, which is
 * the only way the list of things that must be rejected stays checked.
 *
 * Returns either
 *   {ok: true,  design: {circuit, name, params, exported_utc}, warnings: []}
 * or
 *   {ok: false, error: "one sentence saying what is wrong with the file"}
 * and never throws.
 */
(function (root) {
  "use strict";

  //: The schema this build reads. An integer, and the only one accepted.
  var SCHEMA = 1;

  //: A design is a few hundred bytes of JSON. Refusing early means never
  //: parsing a megabyte of something that was never going to be one.
  var MAX_BYTES = 256 * 1024;

  //: A name is a label, not a document.
  var NAME_MAX = 200;

  //: Keys a design may carry. An unknown key means the file was written by
  //: something that knows more than this build does, and quietly dropping
  //: it could quietly drop the part that mattered.
  var KEYS = ["faradaem_design", "app_version", "circuit", "name", "params",
              "measured", "exported_utc"];

  function isPlainObject(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  /* An ISO instant, checked twice: the shape, and then whether the date it
     names exists. 2026-02-31T00:00:00Z has the right shape. */
  function isIsoDate(value) {
    var shape =
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;
    if (typeof value !== "string" || !shape.test(value)) {
      return false;
    }
    var parsed = Date.parse(value);
    if (isNaN(parsed)) {
      return false;
    }
    // Date.parse accepts a day that rolled over into the next month.
    var back = new Date(parsed).toISOString();
    return back.slice(0, 10) === value.slice(0, 10);
  }

  function specFor(circuit, key) {
    var found = null;
    (circuit.params || []).forEach(function (spec) {
      if (spec.key === key) {
        found = spec;
      }
    });
    return found;
  }

  function findCircuit(catalogue, id) {
    var found = null;
    (catalogue || []).forEach(function (item) {
      if (item.id === id) {
        found = item;
      }
    });
    return found;
  }

  function validate(text, options) {
    var settings = options || {};
    var catalogue = settings.catalogue || [];
    var warnings = [];

    function bad(reason) {
      return { ok: false, error: reason };
    }

    if (typeof text !== "string") {
      return bad("That file could not be read as text.");
    }

    var size = settings.bytes !== undefined ? settings.bytes : text.length;
    if (size > MAX_BYTES) {
      return bad("That file is " + Math.round(size / 1024) + " kB. A "
        + "Faradaem design is under " + Math.round(MAX_BYTES / 1024)
        + " kB, so this is not one.");
    }

    var payload;
    try {
      payload = JSON.parse(text);
    } catch (parseError) {
      return bad("That file is not a Faradaem design: it did not parse as "
        + "JSON.");
    }

    if (!isPlainObject(payload)) {
      return bad("That file is not a Faradaem design: the top level is "
        + (Array.isArray(payload) ? "a list" : "not an object") + ".");
    }

    var schema = payload.faradaem_design;
    if (typeof schema !== "number" || !isFinite(schema)
        || Math.floor(schema) !== schema) {
      return bad("That file is not a Faradaem design: it carries no "
        + "faradaem_design version.");
    }
    if (schema !== SCHEMA) {
      return bad("That design is written in schema version " + schema
        + ". This build reads version " + SCHEMA + " only.");
    }

    var unknown = Object.keys(payload).filter(function (key) {
      return KEYS.indexOf(key) === -1;
    });
    if (unknown.length) {
      return bad("That design carries " + unknown.length + " field(s) this "
        + "build does not know: " + unknown.slice(0, 4).join(", ")
        + ". Rather than ignore them and possibly ignore the one that "
        + "mattered, it was not loaded.");
    }

    if (payload.app_version !== undefined
        && typeof payload.app_version !== "string") {
      return bad("That design's app_version is not a string.");
    }
    if (payload.app_version && settings.appVersion
        && payload.app_version !== settings.appVersion) {
      warnings.push("It was exported by Faradaem " + payload.app_version
        + " and this is " + settings.appVersion + ".");
    }

    var circuit = typeof payload.circuit === "string"
      ? findCircuit(catalogue, payload.circuit) : null;
    if (!circuit) {
      return bad("That design names the circuit "
        + JSON.stringify(payload.circuit) + ", which this catalogue does "
        + "not have.");
    }

    if (payload.name !== undefined && payload.name !== null) {
      if (typeof payload.name !== "string") {
        return bad("That design's name is not a string.");
      }
      if (payload.name.length > NAME_MAX) {
        return bad("That design's name is " + payload.name.length
          + " characters, which is not a name.");
      }
    }

    if (!isPlainObject(payload.params)) {
      return bad("That design's params is not an object.");
    }

    var wanted = (circuit.params || []).map(function (spec) {
      return spec.key;
    });
    var missing = wanted.filter(function (key) {
      return payload.params[key] === undefined;
    });
    if (missing.length) {
      return bad("That design is missing " + missing.length + " parameter"
        + (missing.length === 1 ? "" : "s") + " " + circuit.name
        + " needs: " + missing.join(", ") + ".");
    }
    var extra = Object.keys(payload.params).filter(function (key) {
      return wanted.indexOf(key) === -1;
    });
    if (extra.length) {
      return bad("That design carries parameter(s) " + circuit.name
        + " does not have: " + extra.slice(0, 4).join(", ") + ".");
    }

    var clean = {};
    var failure = null;
    wanted.forEach(function (key) {
      if (failure) {
        return;
      }
      var value = payload.params[key];
      // typeof NaN and typeof Infinity are both "number"; isFinite is the
      // check that matters, and JSON cannot even carry them, which means a
      // file containing one was hand-written to try.
      if (typeof value !== "number" || !isFinite(value)) {
        failure = "That design's " + key + " is " + JSON.stringify(value)
          + ", which is not a finite number.";
        return;
      }
      var spec = specFor(circuit, key);
      if (spec && typeof spec.min === "number" && value < spec.min) {
        failure = "That design's " + key + " is " + value + ", below the "
          + "minimum of " + spec.min + " this circuit accepts.";
        return;
      }
      if (spec && typeof spec.max === "number" && value > spec.max) {
        failure = "That design's " + key + " is " + value + ", above the "
          + "maximum of " + spec.max + " this circuit accepts.";
        return;
      }
      clean[key] = value;
    });
    if (failure) {
      return bad(failure);
    }

    if (payload.exported_utc !== undefined && payload.exported_utc !== null
        && !isIsoDate(payload.exported_utc)) {
      return bad("That design's exported_utc is not an ISO date.");
    }

    // measured is read and deliberately dropped. A measurement is a fact
    // about the machine that made it; carrying a stranger's onto this page
    // would be the one thing this whole tool promises not to do.
    if (isPlainObject(payload.measured)
        && Object.keys(payload.measured).length) {
      warnings.push("Its measurements were not loaded: numbers here come "
        + "from this machine's simulator.");
    }

    return {
      ok: true,
      warnings: warnings,
      design: {
        circuit: payload.circuit,
        name: typeof payload.name === "string" ? payload.name : null,
        params: clean,
        exported_utc: typeof payload.exported_utc === "string"
          ? payload.exported_utc : null
      }
    };
  }

  root.FaradaemImport = {
    validate: validate,
    isIsoDate: isIsoDate,
    SCHEMA: SCHEMA,
    MAX_BYTES: MAX_BYTES,
    NAME_MAX: NAME_MAX,
    KEYS: KEYS
  };
}(typeof window !== "undefined" ? window : globalThis));
