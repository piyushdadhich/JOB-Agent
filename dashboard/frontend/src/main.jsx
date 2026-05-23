import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { ThemeProvider } from "./contexts/ThemeContext.jsx";
import { TaskProvider } from "./contexts/TaskContext.jsx";
import { TooltipProvider } from "./components/ui/tooltip.jsx";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <ThemeProvider>
      <TaskProvider>
        <TooltipProvider delayDuration={150}>
          <App />
        </TooltipProvider>
      </TaskProvider>
    </ThemeProvider>
  </StrictMode>,
);
