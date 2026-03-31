const isObject = (value) => value && typeof value === "object" && !Array.isArray(value);

const SINGLE_EXAMPLE = `Valid single strategy example:\n\n\`\`\`yaml\ntype: custom:fortum-energy-single\nfortum:\n  metering_point_number: "6094111"\nitemization:\n  - stat: sensor.sauna_energy\n    name: Sauna\n\`\`\``;

const MULTIPOINT_EXAMPLE = `Valid multipoint strategy example:\n\n\`\`\`yaml\ntype: custom:fortum-energy-multipoint\nmetering_points:\n  - number: "6094111"\n    name: Home\n    itemization:\n      - stat: sensor.sauna_energy\n        name: Sauna\n\`\`\``;

const formatValidationError = (message, strategyType) => {
  const example = strategyType === "multipoint" ? MULTIPOINT_EXAMPLE : SINGLE_EXAMPLE;
  return `${message}\n\n${example}`;
};

const normalizeRequiredString = (value, path) => {
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(Math.trunc(value));
  }
  if (typeof value !== "string") {
    throw new Error(path + " must be a string.");
  }
  const trimmed = value.trim();
  if (!trimmed) {
    throw new Error(path + " must be a non-empty string.");
  }
  return trimmed;
};

const normalizeOptionalString = (value, path) => {
  if (value === undefined || value === null) {
    return undefined;
  }
  if (typeof value !== "string") {
    throw new Error(path + " must be a string when provided.");
  }
  const trimmed = value.trim();
  return trimmed || undefined;
};

const normalizeItemization = (itemization, path) => {
  if (!Array.isArray(itemization)) {
    throw new Error(path + " must be a list.");
  }
  return itemization.map((entry, index) => {
    if (!isObject(entry)) {
      throw new Error(`${path}[${index}] must be an object.`);
    }
    const statConsumption = normalizeRequiredString(
      entry.stat,
      `${path}[${index}].stat`
    );
    const name = normalizeOptionalString(entry.name, `${path}[${index}].name`);
    return {
      stat: statConsumption,
      ...(name ? { name } : {}),
    };
  });
};

const validateSingleStrategyConfigCore = (config) => {
  if (!isObject(config)) {
    throw new Error("strategy config must be an object.");
  }

  const validated = { ...config };
  if (Object.prototype.hasOwnProperty.call(validated, "debug")) {
    if (typeof validated.debug !== "boolean") {
      throw new Error("strategy.debug must be a boolean when provided.");
    }
  }

  if (validated.fortum !== undefined) {
    if (!isObject(validated.fortum)) {
      throw new Error("strategy.fortum must be an object when provided.");
    }
    const fortum = { ...validated.fortum };
    if (fortum.metering_point_number !== undefined) {
      fortum.metering_point_number = normalizeRequiredString(
        fortum.metering_point_number,
        "strategy.fortum.metering_point_number"
      );
    }
    validated.fortum = fortum;
  }

  if (Object.prototype.hasOwnProperty.call(validated, "itemization")) {
    validated.itemization = normalizeItemization(validated.itemization, "strategy.itemization");
  }

  return validated;
};

export const validateSingleStrategyConfig = (config) => {
  try {
    return validateSingleStrategyConfigCore(config);
  } catch (err) {
    const message = err && err.message ? err.message : String(err);
    throw new Error(formatValidationError(message, "single"));
  }
};

export const validateMultipointStrategyConfig = (config) => {
  try {
    const validated = validateSingleStrategyConfigCore(config);
    if (!Array.isArray(validated.metering_points) || validated.metering_points.length === 0) {
      throw new Error("strategy.metering_points must be a non-empty list.");
    }

    validated.metering_points = validated.metering_points.map((point, index) => {
      if (!isObject(point)) {
        throw new Error(`strategy.metering_points[${index}] must be an object.`);
      }
      const number = normalizeRequiredString(
        point.number,
        `strategy.metering_points[${index}].number`
      );
      const name = normalizeOptionalString(
        point.name,
        `strategy.metering_points[${index}].name`
      );
      const address = normalizeOptionalString(
        point.address,
        `strategy.metering_points[${index}].address`
      );

      if (!Object.prototype.hasOwnProperty.call(point, "itemization")) {
        throw new Error(`strategy.metering_points[${index}].itemization must be a list.`);
      }

      return {
        number,
        ...(name ? { name } : {}),
        ...(address ? { address } : {}),
        itemization: normalizeItemization(
          point.itemization,
          `strategy.metering_points[${index}].itemization`
        ),
      };
    });

    return validated;
  } catch (err) {
    const message = err && err.message ? err.message : String(err);
    throw new Error(formatValidationError(message, "multipoint"));
  }
};
