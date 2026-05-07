import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default:     "bg-accent text-canvas hover:bg-accent/90 shadow-accent-glow",
        secondary:   "bg-elevated text-text-primary hover:bg-border",
        ghost:       "text-text-secondary hover:bg-elevated hover:text-text-primary",
        destructive: "bg-error/15 text-error border border-error/40 hover:bg-error/25",
        outline:     "border border-border bg-canvas hover:bg-elevated",
      },
      size: {
        default: "h-7 px-3",
        icon:    "h-7 w-7",
        lg:      "h-9 px-4 text-sm",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />;
  },
);
Button.displayName = "Button";

export { buttonVariants };
