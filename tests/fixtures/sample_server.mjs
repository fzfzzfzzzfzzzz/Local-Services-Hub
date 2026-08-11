import http from "node:http";

const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(process.argv[index], process.argv[index + 1]);
}

const host = args.get("--host") ?? "127.0.0.1";
const port = Number(args.get("--port"));
const name = args.get("--name") ?? "node-sample";

if (!Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error("--port must be a valid integer");
}

const server = http.createServer((request, response) => {
  if (request.url === "/health") {
    response.writeHead(200, { "content-type": "application/json; charset=utf-8" });
    response.end(JSON.stringify({ status: "ok", service: name }));
    return;
  }

  response.writeHead(200, { "content-type": "text/plain; charset=utf-8" });
  response.end(`${name} is running\n`);
});

server.listen(port, host, () => {
  console.log(`${name} listening on http://${host}:${port}`);
});

