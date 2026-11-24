
import asyncio
from asyncua import Client, ua

async def main():
    url = "opc.tcp://192.168.1.2:4840"

    async with Client(url=url) as client:
        print("PLC bağlantısı başarılı!\n")
        print("PLC değişkenleri taranıyor...\n")

        objects = client.get_node(ua.ObjectIds.ObjectsFolder)

        async def browse(node, depth=0):
            if depth > 8:
                return

            try:
                children = await node.get_children()

                for child in children:
                    try:
                        name = await child.read_browse_name()
                        node_class = await child.read_node_class()

                        if node_class == ua.NodeClass.Variable:
                            value = await child.read_value()
                            print("-" * 50)
                            print("Değişken:", name.Name)
                            print("Node ID:", child.nodeid.to_string())
                            print("Değer:", value)

                        elif node_class == ua.NodeClass.Object:
                            print("\nKlasör:", name.Name)
                            print("Node ID:", child.nodeid.to_string())

                            await browse(child, depth + 1)

                    except Exception as e:
                        print("Düğüm okunamadı:", e)

            except Exception as e:
                print("Klasör taranamadı:", e)

        await browse(objects)

        print("\nTarama tamamlandı.")

if __name__ == "__main__":
    asyncio.run(main())
