
import asyncio
from asyncua import Client, ua

async def browse_node(node, depth=0, visited=None):
    if visited is None:
        visited = set()

    node_id = str(node.nodeid)

    if node_id in visited or depth > 10:
        return

    visited.add(node_id)

    try:
        name = await node.read_browse_name()
        node_class = await node.read_node_class()

        print(
            "  " * depth,
            name.Name,
            "|",
            node_class.name,
            "|",
            node_id
        )

        children = await node.get_children()

        for child in children:
            await browse_node(child, depth + 1, visited)

    except Exception as e:
        print("Hata:", node_id, e)


async def main():
    url = "opc.tcp://192.168.1.2:4840"

    async with Client(url=url) as client:
        print("PLC bağlantısı başarılı!")
        print("\nOPC UA ADRES ALANI TARAMASI\n")

        objects = client.get_objects_node()

        await browse_node(objects)


asyncio.run(main())
