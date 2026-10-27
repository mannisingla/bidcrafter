import { LucideAngularModule } from 'lucide-angular';
import {
  ArrowDown, ArrowUp, Bell, Bold, BookOpen, Bookmark, Briefcase, Check, ChevronLeft, ChevronRight, ClipboardCheck,
  Clock, Download, Eye, File, FilePlus, FileSearch, FileText, Files, Flag, FolderPlus, GripVertical, Hash, Heading, Info,
  Italic, Layers, LayoutDashboard, Library, List, ListOrdered, PanelLeft, Pencil, PenTool, Plus, Printer, RotateCcw,
  ScrollText, ShieldCheck, Sparkles, TextCursorInput, Trash2, Underline, Upload, User, Users, X, ZoomIn, ZoomOut,
} from 'lucide-angular';

/** Icon set shared by all screens; names match the prototype's data-lucide values. Registered in app.config.ts. */
export const Icons = LucideAngularModule;
export const ICON_PROVIDERS = LucideAngularModule.pick({
  ArrowDown, ArrowUp, Bell, Bold, BookOpen, Bookmark, Briefcase, Check, ChevronLeft, ChevronRight, ClipboardCheck,
  Clock, Download, Eye, File, FilePlus, FileSearch, FileText, Files, Flag, FolderPlus, GripVertical, Hash, Heading, Info,
  Italic, Layers, LayoutDashboard, Library, List, ListOrdered, PanelLeft, Pencil, PenTool, Plus, Printer, RotateCcw,
  ScrollText, ShieldCheck, Sparkles, TextCursorInput, Trash2, Underline, Upload, User, Users, X, ZoomIn, ZoomOut,
});
