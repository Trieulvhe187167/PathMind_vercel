"""All tables. Importing this package registers every model on ``Base.metadata``."""

from ._base import new_id, utcnow
from .accounts import (
    ROLE_GRANTORS, ROLES, AuditLog, AuthSession, RoleRequest, User, UserIdentity, UserInvitation,
    VerificationCode,
)
from .agents import MCPServer, Memory, MemoryInsight, MemorySharingConsent, Partner, PartnerShare, Persona, Skill
from .billing import FeatureUsage, LLMCall, Payment, Plan, Subscription, UsageByModel, UsageDaily
from .chat import (
    ChatMessage, ChatMessageCitation, ChatSession, ChatSessionKnowledgeBase, ChatTurn, ChatTurnEvent,
)
from .community import (
    CommunitySkill, CommunitySkillInstall, ContentReport, ForumComment, ForumPost, Notification, Vote,
)
from .jobs import BackgroundTask, ScheduledJob
from .knowledge import KnowledgeBase, KnowledgeBaseAccess, KnowledgeBaseDocument, StoredFile
from .knowledge_tree import (
    AnswerFeedback, Contribution, ContributionEvent, ContributionFile, KnowledgeChunk, KnowledgeDomain,
    KnowledgeGap, KnowledgeNode, KnowledgeNodeDocument, KnowledgeNodeVersion, RewardLedger, TreeChange,
    TreeChangeSet, UserBadge, VerifiedDocument,
)
from .learning import (
    Book, BookChapter, Course, CourseKnowledgeBase, LearningEvidence, LearningPath, LearningSession,
    LearningTopic, LearningTopicSource, Notebook, NotebookEntry, NotebookEntryTag, PathNode,
    PracticeAttempt, PracticeImport, Question, ReadingAnnotation, ReadingDocument, ReviewState, Tag,
    WritingDocument, WritingRevision,
)
from .organization import BulkImport, BulkImportRow, LearnerGroup, LearnerGroupMember
from .social import Friendship, RoomMember, RoomMessage, StudyRoom
from .system import AIModel, AIProvider, AppSetting, ModelPrice, StorageBackend

__all__ = [name for name in dir() if not name.startswith("_")]
