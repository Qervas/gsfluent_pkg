import { useStore } from "./store";

/** Returns true when the user has edited the currently-loaded recipe
 *  and not yet saved. Compares the live `activeRecipeData` against the
 *  pristine snapshot the store captured at `loadActiveRecipe` time.
 *
 *  Why JSON.stringify instead of deep-equal: recipes are small (~50
 *  fields), all JSON-serializable. JSON.stringify is fast at this size
 *  and avoids pulling in a deep-equal dep. Field-ordering doesn't
 *  matter because both inputs went through the same shape (server
 *  response → store → panel edits via {...spread}).
 *
 *  Returns false when no recipe is loaded. */
export function useRecipeDirty(): boolean {
  return useStore((s) => {
    if (!s.activeRecipeData || !s.activeRecipePristine) return false;
    return JSON.stringify(s.activeRecipeData) !== JSON.stringify(s.activeRecipePristine);
  });
}
