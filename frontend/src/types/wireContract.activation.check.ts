/**
 * Wire contract, Activation domain (audit 2026-09-21 quality-04, D-api-types-a3 P1):
 * propertyLookup, activation and telemetry.
 *
 * Compile-time assertions only, checked by tsc through `include: ["src"]`;
 * nothing imports this file. (i) pairs every schema-named hand type with its
 * schema; (ii) pairs every bound call site whose type argument is not
 * schema-named with its operation. tests/unit/test_frontend_wire_contract.py
 * proves the coverage and the binding. A failing pair carries a dated
 * wire-drift entry tagged to the lane that fixes it at the cause.
 */
import type { ApiRequest, ApiResponse } from './api.gen';
import type { DeepNoPhantomKeys, Expect, NoPhantomKeys, WireFits } from './wireContract.check';
import type { ActivationStageRequest } from '../lib/apiClients/activation';
import type { RumEvent } from '../lib/rum';
import type { ActivationDestination, ActivationOutboxItem, ActivationStageResponse, ActivationSummary } from '../types';
import type { PropertyLoanLookupLoan, PropertyLoanLookupRequest, PropertyLoanLookupResponse } from './propertyLookup';

export type WireContractActivation = [
  // (i) schema-named hand types
  Expect<WireFits<ActivationStageRequest, ApiRequest<'ActivationStageRequest'>>>,
  Expect<DeepNoPhantomKeys<ActivationStageRequest, ApiRequest<'ActivationStageRequest'>>>,
  Expect<WireFits<ApiResponse<'ActivationDestination'>, ActivationDestination>>,
  Expect<NoPhantomKeys<ActivationDestination, ApiResponse<'ActivationDestination'>>>,
  Expect<WireFits<ApiResponse<'ActivationOutboxItem'>, ActivationOutboxItem>>,
  Expect<NoPhantomKeys<ActivationOutboxItem, ApiResponse<'ActivationOutboxItem'>>>,
  Expect<WireFits<ApiResponse<'ActivationStageResponse'>, ActivationStageResponse>>,
  Expect<NoPhantomKeys<ActivationStageResponse, ApiResponse<'ActivationStageResponse'>>>,
  Expect<WireFits<ApiResponse<'ActivationSummary'>, ActivationSummary>>,
  Expect<NoPhantomKeys<ActivationSummary, ApiResponse<'ActivationSummary'>>>,
  Expect<WireFits<ApiResponse<'PropertyLoanLookupLoan'>, PropertyLoanLookupLoan>>,
  Expect<NoPhantomKeys<PropertyLoanLookupLoan, ApiResponse<'PropertyLoanLookupLoan'>>>,
  Expect<WireFits<PropertyLoanLookupRequest, ApiRequest<'PropertyLoanLookupRequest'>>>,
  Expect<DeepNoPhantomKeys<PropertyLoanLookupRequest, ApiRequest<'PropertyLoanLookupRequest'>>>,
  Expect<WireFits<ApiResponse<'PropertyLoanLookupResponse'>, PropertyLoanLookupResponse>>,
  Expect<NoPhantomKeys<PropertyLoanLookupResponse, ApiResponse<'PropertyLoanLookupResponse'>>>,
  // (ii) bound call sites whose type argument is not schema-named
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-field-vitals: navigation_type is string but the wire takes only navigate | reload | back_forward | prerender
  Expect<WireFits<RumEvent, ApiRequest<'RumEvent'>>>,
  // @ts-expect-error wire-drift quality-04 2026-10-01 w5-field-vitals: details is an open Record<string, ...> but the wire RumEvent.details is a closed set of reviewed keys
  Expect<DeepNoPhantomKeys<RumEvent, ApiRequest<'RumEvent'>>>,
];
