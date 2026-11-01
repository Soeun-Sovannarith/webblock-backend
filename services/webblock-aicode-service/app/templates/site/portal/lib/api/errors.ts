/** RFC 7807 problem details, as returned by the Spring API. */
export type Problem = {
  title: string;
  status: number;
  detail?: string;
};

export class ApiError extends Error {
  readonly title: string;
  readonly status: number;
  readonly detail?: string;

  constructor(problem: Problem) {
    super(problem.detail ?? problem.title);
    this.name = "ApiError";
    this.title = problem.title;
    this.status = problem.status;
    this.detail = problem.detail;
  }
}
