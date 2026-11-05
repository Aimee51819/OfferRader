import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import DuplicateNotice from "./DuplicateNotice";

const match = {
  application_id: "a1", role_title: "Product Manager Intern", external_job_id: "84211", posting_url: null,
  recruiting_cycle: "Summer 2027", stage: "acknowledged", submitted_at: null,
};

describe("DuplicateNotice", () => {
  it("shows BLOCK with the exact-opening rationale and a link", () => {
    render(
      <MemoryRouter>
        <DuplicateNotice check={{ decision: "BLOCK", reasons: ["Already applied to this exact opening (requisition 84211)."], company_id: "c", matches: [{ ...match, match_kind: "exact_opening", level: "BLOCK" }] }} />
      </MemoryRouter>,
    );
    expect(screen.getByText("BLOCK")).toBeInTheDocument();
    expect(screen.getByText(/exact opening/i, { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Product Manager Intern" })).toHaveAttribute("href", "/applications/a1");
  });

  it("shows WARN, not a block, for another role at the same employer", () => {
    render(
      <MemoryRouter>
        <DuplicateNotice check={{ decision: "WARN", reasons: ["You also have a different role here"], company_id: "c", matches: [{ ...match, match_kind: "other_role_same_company", level: "WARN" }] }} />
      </MemoryRouter>,
    );
    expect(screen.getByText("WARN")).toBeInTheDocument();
    expect(screen.getByText(/Different role, same employer/)).toBeInTheDocument();
  });

  it("renders nothing without a check", () => {
    const { container } = render(<DuplicateNotice check={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
