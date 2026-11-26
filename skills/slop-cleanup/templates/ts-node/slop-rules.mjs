// Local oxlint rules for the slop-cleanup TypeScript template (loaded through jsPlugins in .oxlintrc.json).
// no-focused-test: node:test focus (test.only, describe.only, { only: true }) skips the rest of the suite.
// delete-needs-where, update-needs-where: a Drizzle db.delete() or db.update() with no .where() changes every
// row. Remove these two in a repository that does not use the Drizzle query builder.
const RUNNERS = new Set(['test', 'it', 'describe', 'suite']);
const isOnly = (c) => c.type === 'MemberExpression' && !c.computed && c.property.name === 'only' && c.object.type === 'Identifier' && RUNNERS.has(c.object.name);
const onlyOption = (n) => n.callee.type === 'Identifier' && RUNNERS.has(n.callee.name) && n.arguments.some((a) => a.type === 'ObjectExpression' && a.properties.some((p) => p.key?.name === 'only' && p.value?.value === true));
function needsWhere(method) {
  return {
    meta: { schema: [{ type: 'object', properties: { drizzleObjectName: { type: 'array', items: { type: 'string' } } }, additionalProperties: false }] },
    create(context) {
      const objects = new Set(context.options[0]?.drizzleObjectName ?? ['db']);
      return {
        CallExpression(n) {
          const c = n.callee;
          if (c.type !== 'MemberExpression' || c.computed || c.property.name !== method || c.object.type !== 'Identifier' || !objects.has(c.object.name)) return;
          let up = n.parent, chained = false;
          while (up && (up.type === 'MemberExpression' || up.type === 'CallExpression')) { if (up.type === 'MemberExpression' && up.property.name === 'where') chained = true; up = up.parent; }
          if (!chained) context.report({ node: n, message: `${method}() without .where() changes every row` });
        },
      };
    },
  };
}
export default {
  meta: { name: 'slop' },
  rules: {
    'no-focused-test': { create: (context) => ({ CallExpression(n) { if (isOnly(n.callee) || onlyOption(n)) context.report({ node: n, message: 'Focused test skips the rest of the suite' }); } }) },
    'delete-needs-where': needsWhere('delete'),
    'update-needs-where': needsWhere('update'),
  },
};
