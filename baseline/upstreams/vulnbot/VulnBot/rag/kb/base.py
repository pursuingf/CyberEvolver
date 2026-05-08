import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Tuple, Union

from langchain.docstore.document import Document

from config.config import Configs

from rag.kb.models.kb_document_model import MatchDocument, KnowledgeBaseSchema
from rag.kb.repository.kb_repository import add_kb_to_db, load_kb_from_db, list_kbs_from_db, kb_exists, \
    delete_kb_from_db
from rag.kb.repository.knowledge_file_repository import get_file_detail, list_docs_from_db, list_files_from_db, \
    count_files_from_db, file_exists_in_db, delete_files_from_db, add_file_to_db, delete_file_from_db
from rag.kb.utils.kb_utils import get_kb_path, get_doc_path, list_files_from_folder, KnowledgeFile, list_kbs_from_folder
from utils.log_common import build_logger

logger = build_logger()

class SupportedVSType:
    MILVUS = "milvus"


class KBService(ABC):
    def __init__(
            self,
            knowledge_base_name: str,
            kb_info: str = None,
            embed_model: str = Configs.llm_config.embedding_models,
    ):
        self.kb_name = knowledge_base_name
        self.kb_info = kb_info
        self.embed_model = embed_model
        self.kb_path = get_kb_path(self.kb_name)
        self.doc_path = get_doc_path(self.kb_name)
        self.do_init()

    def __repr__(self) -> str:
        return f"{self.kb_name} @ {self.embed_model}"

    def save_vector_store(self):
        """
        保存向量库:FAISS保存到磁盘，milvus保存到数据库。PGVector暂未支持
        """
        pass

    def create_kb(self):
        """
        创建知识库
        """
        if not os.path.exists(self.doc_path):
            os.makedirs(self.doc_path)

        status = add_kb_to_db(
            self.kb_name, self.kb_info, self.vs_type(), self.embed_model
        )

        if status:
            self.do_create_kb()
        return status

    def clear_vs(self):
        """
        删除向量库中所有内容
        """
        self.do_clear_vs()
        status = delete_files_from_db(self.kb_name)
        return status

    def drop_kb(self):
        """
        删除知识库
        """
        self.do_drop_kb()
        status = delete_kb_from_db(self.kb_name)
        return status

    def add_doc(self, kb_file: KnowledgeFile, docs: List[Document] = [], **kwargs):
        """
        向知识库添加文件
        如果指定了docs，则不再将文本向量化，并将数据库对应条目标为custom_docs=True
        """

        if docs:
            custom_docs = True
        else:
            docs = kb_file.file2text()
            custom_docs = False

        if docs:
            # 将 metadata["source"] 改为相对路径
            for doc in docs:
                try:
                    doc.metadata.setdefault("source", kb_file.filename)
                    source = doc.metadata.get("source", "")
                    if os.path.isabs(source):
                        rel_path = Path(source).relative_to(self.doc_path)
                        doc.metadata["source"] = str(rel_path.as_posix().strip("/"))
                except Exception as e:
                    print(
                        f"cannot convert absolute path ({source}) to relative path. error is : {e}"
                    )
            self.delete_doc(kb_file)
            doc_infos = self.do_add_doc(docs, **kwargs)
            status = add_file_to_db(
                kb_file,
                custom_docs=custom_docs,
                docs_count=len(docs),
                doc_infos=doc_infos,
            )
        else:
            status = False
        return status

    def delete_doc(
            self, kb_file: KnowledgeFile, delete_content: bool = False, **kwargs
    ):
        """
        从知识库删除文件
        """
        self.do_delete_doc(kb_file, **kwargs)
        status = delete_file_from_db(kb_file)
        if delete_content and os.path.exists(kb_file.filepath):
            os.remove(kb_file.filepath)
        return status

    def update_info(self, kb_info: str):
        """
        更新知识库介绍
        """
        self.kb_info = kb_info
        status = add_kb_to_db(
            self.kb_name, self.kb_info, self.vs_type(), self.embed_model
        )
        return status

    def update_doc(self, kb_file: KnowledgeFile, docs: List[Document] = [], **kwargs):
        """
        使用content中的文件更新向量库
        如果指定了docs，则使用自定义docs，并将数据库对应条目标为custom_docs=True
        """

        if os.path.exists(kb_file.filepath):
            self.delete_doc(kb_file, **kwargs)
            return self.add_doc(kb_file, docs=docs, **kwargs)

    def exist_doc(self, file_name: str):
        return file_exists_in_db(
            KnowledgeFile(knowledge_base_name=self.kb_name, filename=file_name)
        )

    def list_files(self):
        return list_files_from_db(self.kb_name)

    def count_files(self):
        return count_files_from_db(self.kb_name)

    def search_docs(
            self,
            query: str,
            top_k: int ,
            score_threshold: float,
    ) -> List[Document]:

        docs = self.do_search(query, top_k, score_threshold)
        return docs

    def get_doc_by_ids(self, ids: List[str]) -> List[Document]:
        return []

    def del_doc_by_ids(self, ids: List[str]) -> bool:
        raise NotImplementedError

    def update_doc_by_ids(self, docs: Dict[str, Document]) -> bool:
        """
        传入参数为： {doc_id: Document, ...}
        如果对应 doc_id 的值为 None，或其 page_content 为空，则删除该文档
        """
        self.del_doc_by_ids(list(docs.keys()))
        pending_docs = []
        ids = []
        for _id, doc in docs.items():
            if not doc or not doc.page_content.strip():
                continue
            ids.append(_id)
            pending_docs.append(doc)
        self.do_add_doc(docs=pending_docs, ids=ids)
        return True

    def list_docs(
            self, file_name: str = None, metadata: Dict = {}
    ) -> List[MatchDocument]:
        """
        通过file_name或metadata检索Document
        """
        doc_infos = list_docs_from_db(
            kb_name=self.kb_name, file_name=file_name, metadata=metadata
        )
        docs = []
        for x in doc_infos:
            doc_info = self.get_doc_by_ids([x["id"]])[0]
            if doc_info is not None:
                # 处理非空的情况
                doc_with_id = MatchDocument(**{**doc_info.dict(), "id":x["id"]})
                docs.append(doc_with_id)
            else:
                # 处理空的情况
                # 可以选择跳过当前循环迭代或执行其他操作
                pass
        return docs


    @abstractmethod
    def do_create_kb(self):
        """
        创建知识库子类实自己逻辑
        """
        pass

    @classmethod
    def list_kbs(cls):
        return list_kbs_from_db()

    def exists(self, kb_name: str = None):
        kb_name = kb_name or self.kb_name
        return kb_exists(kb_name)

    @abstractmethod
    def vs_type(self) -> str:
        pass

    @abstractmethod
    def do_init(self):
        pass

    @abstractmethod
    def do_drop_kb(self):
        """
        删除知识库子类实自己逻辑
        """
        pass

    @abstractmethod
    def do_search(
            self,
            query: str,
            top_k: int,
            score_threshold: float,
    ) -> List[Tuple[Document, float]]:
        """
        搜索知识库子类实自己逻辑
        """
        pass

    @abstractmethod
    def do_add_doc(
            self,
            docs: List[Document],
            **kwargs,
    ) -> List[Dict]:
        """
        向知识库添加文档子类实自己逻辑
        """
        pass

    @abstractmethod
    def do_delete_doc(self, kb_file: KnowledgeFile):
        """
        从知识库删除文档子类实自己逻辑
        """
        pass

    @abstractmethod
    def do_clear_vs(self):
        """
        从知识库删除全部向量子类实自己逻辑
        """
        pass


class KBServiceFactory:
    @staticmethod
    def get_service(
            kb_name: str,
            vector_store_type: Union[str, SupportedVSType],
            embed_model: str,
            kb_info: str = None,
    ) -> KBService:
        if isinstance(vector_store_type, str):
            vector_store_type = getattr(SupportedVSType, vector_store_type.upper())
        params = {
            "knowledge_base_name": kb_name,
            "embed_model": embed_model,
            "kb_info": kb_info,
        }
        if SupportedVSType.MILVUS == vector_store_type:
            from rag.kb.service.milvus_kb_service import MilvusKBService
            return MilvusKBService(**params)

    @staticmethod
    def get_service_by_name(kb_name: str) -> KBService:
        _, vs_type, embed_model = load_kb_from_db(kb_name)
        if _ is None:  # kb not in db, just return None
            return None
        return KBServiceFactory.get_service(kb_name, vs_type, embed_model)



def get_kb_details() -> List[Dict]:
    kbs_in_folder = list_kbs_from_folder()
    kbs_in_db: List[KnowledgeBaseSchema] = KBService.list_kbs()
    result = {}

    for kb in kbs_in_folder:
        result[kb] = {
            "kb_name": kb,
            "vs_type": "",
            "kb_info": "",
            "embed_model": "",
            "file_count": 0,
            "create_time": None,
            "in_folder": True,
            "in_db": False,
        }

    for kb_detail in kbs_in_db:
        kb_detail = kb_detail.model_dump()
        kb_name = kb_detail["kb_name"]
        kb_detail["in_db"] = True
        if kb_name in result:
            result[kb_name].update(kb_detail)
        else:
            kb_detail["in_folder"] = False
            result[kb_name] = kb_detail

    data = []
    for i, v in enumerate(result.values()):
        v["No"] = i + 1
        data.append(v)

    return data

def get_kb_file_details(kb_name: str) -> List[Dict]:
    kb = KBServiceFactory.get_service_by_name(kb_name)
    if kb is None:
        return []

    files_in_folder = list_files_from_folder(kb_name)
    files_in_db = kb.list_files()
    result = {}

    for doc in files_in_folder:
        result[doc] = {
            "kb_name": kb_name,
            "file_name": doc,
            "file_ext": os.path.splitext(doc)[-1],
            "file_version": 0,
            "document_loader": "",
            "docs_count": 0,
            "text_splitter": "",
            "create_time": None,
            "in_folder": True,
            "in_db": False,
        }
    lower_names = {x.lower(): x for x in result}
    for doc in files_in_db:
        doc_detail = get_file_detail(kb_name, doc)
        if doc_detail:
            doc_detail["in_db"] = True
            if doc.lower() in lower_names:
                result[lower_names[doc.lower()]].update(doc_detail)
            else:
                doc_detail["in_folder"] = False
                result[doc] = doc_detail

    data = []
    for i, v in enumerate(result.values()):
        v["No"] = i + 1
        data.append(v)

    return data


