import { motion, AnimatePresence } from "framer-motion";
import avatarImg from "@/assets/avatar-teacher.png";

export default function AvatarPanel({
  isSpeaking,
  pip = false,
}: {
  isSpeaking: boolean;
  pip?: boolean;
}) {
  return (
    <div className="h-full w-full relative overflow-hidden bg-[#0e1320]">

      <img
        src={avatarImg}
        alt="Prof IA"
        className="w-full h-full"
        style={{
          objectFit: "cover",
          objectPosition: pip ? "center 33%" : "center 20%",
          width: "130%",
          height: "130%",
          marginLeft: "0%",
          marginTop: "-25%",
          display: "block",
        }}
      />

      <AnimatePresence>
        {isSpeaking && (
          <motion.div
            key="speak-overlay"
            className="absolute inset-0 pointer-events-none"
            style={{ background: "rgba(167,139,250,0.07)" }}
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 1, 0] }}
            exit={{ opacity: 0 }}
            transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
          />
        )}
      </AnimatePresence>

      <div
        className="absolute inset-x-0 bottom-0 h-16 pointer-events-none"
        style={{ background: "linear-gradient(to top, rgba(14,19,32,0.85) 0%, transparent 100%)" }}
      />

    </div>
  );
}