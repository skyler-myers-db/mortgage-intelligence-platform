import { describe, expect, it } from 'vitest';
import { DRAWER_SOURCES } from './drawerSources';
import {
  ADDRESSABLE_POPULATION_KPI_LABEL,
  MARKETABLE_POPULATION_KPI_LABEL,
  populationKpiLabel,
} from './populationLabels';

describe('populationKpiLabel', () => {
  it('only calls a count marketable when the contactability gate was applied', () => {
    expect(populationKpiLabel('Eligible only')).toBe(MARKETABLE_POPULATION_KPI_LABEL);
    // "Any" leaves suppressed + DNC borrowers in the count (Home's predicate),
    // and "Suppressed only" is the complement — neither is marketable.
    expect(populationKpiLabel('Any')).toBe(ADDRESSABLE_POPULATION_KPI_LABEL);
    expect(populationKpiLabel('Suppressed only')).toBe(ADDRESSABLE_POPULATION_KPI_LABEL);
    expect(populationKpiLabel(undefined)).toBe(ADDRESSABLE_POPULATION_KPI_LABEL);
  });
});

describe('population evidence chips', () => {
  it('gives the two population cuts distinct chip copy', () => {
    expect(DRAWER_SOURCES.population.short).toBe(ADDRESSABLE_POPULATION_KPI_LABEL);
    expect(DRAWER_SOURCES.populationMarketable.short).toBe(
      `${MARKETABLE_POPULATION_KPI_LABEL} — contact-eligible subset`,
    );
    expect(DRAWER_SOURCES.populationMarketable.short).not.toBe(DRAWER_SOURCES.population.short);
    // Same governed asset and lineage family — only the predicate differs.
    expect(DRAWER_SOURCES.populationMarketable.assetPath).toBe(DRAWER_SOURCES.population.assetPath);
    expect(DRAWER_SOURCES.populationMarketable.lineageFamily).toBe(
      DRAWER_SOURCES.population.lineageFamily,
    );
  });
});
