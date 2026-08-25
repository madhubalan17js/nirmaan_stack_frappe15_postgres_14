import { Terminal } from "lucide-react"

import {
    Alert,
    AlertDescription,
    AlertTitle,
} from "@/components/ui/alert"

interface ErrorAlertProps {
    error?: any;
    /** Forwarded to the underlying Alert, for call sites that place it in a layout. */
    className?: string;
}

export function AlertDestructive({ error, className }: ErrorAlertProps) {
    return (
        <Alert variant="destructive" className={className}>
            <Terminal className="h-4 w-4" />
            <AlertTitle>Error</AlertTitle>
            <AlertDescription>
                {error?.message || "An unknown error occurred."}
            </AlertDescription>
        </Alert>
    )
}
