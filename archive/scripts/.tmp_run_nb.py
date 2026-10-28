import nbformat
from nbclient import NotebookClient
p='notebooks/02_bi_analyst.ipynb'
nb=nbformat.read(p,as_version=4)
client=NotebookClient(nb,timeout=360,kernel_name='python3',allow_errors=False)
client.execute(cwd='.')
nbformat.write(nb,p)
print('Notebook 2 executed successfully; outputs saved.')
for cell in nb.cells:
    if cell.cell_type=='code':
        for out in cell.get('outputs',[]):
            if out.output_type=='stream' and 'Modèle de synthèse :' in out.text:
                print(out.text[-800:])
