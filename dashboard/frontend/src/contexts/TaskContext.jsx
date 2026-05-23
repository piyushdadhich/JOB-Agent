import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { api } from "@/api";

// Tracks the one active background batch task (generate-all /
// regenerate-all) at the app level. The poll loop lives in the
// provider, so progress survives page navigation — any page can
// read `activeTask` and the GlobalTaskBanner renders it everywhere.

const TaskContext = createContext({
  activeTask: null,
  startTask: () => {},
  dismissTask: () => {},
});

const POLL_MS = 3000;

export function TaskProvider({ children }) {
  // activeTask: { type, label, task_id, total, completed, status,
  //   current_opp_id, current_employer, errors, startedAt } | null
  const [activeTask, setActiveTask] = useState(null);
  const pollRef = useRef(null);

  const stopPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startTask = useCallback((type, label, taskId, total) => {
    setActiveTask({
      type,
      label,
      task_id: taskId,
      total,
      completed: 0,
      status: total > 0 ? "running" : "done",
      current_opp_id: null,
      current_employer: null,
      errors: [],
      startedAt: Date.now(),
    });
  }, []);

  const dismissTask = useCallback(() => {
    stopPoll();
    setActiveTask(null);
  }, [stopPoll]);

  const taskId = activeTask?.task_id;
  const status = activeTask?.status;

  useEffect(() => {
    if (!taskId || status !== "running") {
      stopPoll();
      return;
    }
    if (pollRef.current) return; // already polling this task
    pollRef.current = setInterval(async () => {
      try {
        const p = await api.applicationsGenerateAllProgress(taskId);
        setActiveTask((cur) =>
          cur && cur.task_id === taskId ? { ...cur, ...p } : cur,
        );
        if (p.status !== "running") stopPoll();
      } catch (e) {
        setActiveTask((cur) =>
          cur && cur.task_id === taskId
            ? { ...cur, status: "error", errors: [String(e)] }
            : cur,
        );
        stopPoll();
      }
    }, POLL_MS);
    return () => {};
  }, [taskId, status, stopPoll]);

  return (
    <TaskContext.Provider value={{ activeTask, startTask, dismissTask }}>
      {children}
    </TaskContext.Provider>
  );
}

export function useTask() {
  return useContext(TaskContext);
}
