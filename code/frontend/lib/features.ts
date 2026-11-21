/**
 * Feature switch for the college side (staff desk, campus walkthroughs). Off by default: Samyukta is a student-facing product.
 * To bring the college side back, build with NEXT_PUBLIC_COLLEGE_MODE=true (and run the API with COLLEGE_MODE=true).
 */
export const COLLEGE_MODE = process.env.NEXT_PUBLIC_COLLEGE_MODE === "true";
