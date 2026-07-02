import { getFeatures, type Lang } from "@/lib/i18n";
import { Sparkles, Globe, BookOpen, User, Settings, ShieldCheck, Lightbulb, Mic } from "lucide-react";

const icons = [Sparkles, Globe, BookOpen, User, Settings, ShieldCheck, Lightbulb, Mic];

export default function FeatureCarousel({ lang }: { lang: Lang }) {
  const features = getFeatures(lang);
  const items = [...features, ...features]; // duplicate for seamless loop

  return (
    <div className="relative overflow-hidden py-12 group">
      <div className="absolute inset-y-0 left-0 w-24 bg-gradient-to-r from-white/85 to-transparent z-10" />
      <div className="absolute inset-y-0 right-0 w-24 bg-gradient-to-l from-white/85 to-transparent z-10" />
      <div className="flex animate-scroll group-hover:[animation-play-state:paused]">
        {items.map((feature, i) => {
          const Icon = icons[i % icons.length];
          return (
            <div
              key={i}
              className="flex-shrink-0 mx-3 px-6 py-3 rounded-full glass-card flex items-center gap-2.5 text-sm font-medium text-foreground/80 hover:shadow-hover transition-shadow duration-300"
            >
              <Icon className="w-4 h-4 text-primary" />
              {feature}
            </div>
          );
        })}
      </div>
    </div>
  );
}
