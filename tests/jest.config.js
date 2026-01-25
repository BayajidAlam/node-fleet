module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
  roots: ["<rootDir>"],
  testMatch: ["**/__tests__/**/*.ts", "**/?(*.)+(spec|test).ts"],
  collectCoverageFrom: [
    "tests/**/*.ts",
    "!tests/**/*.test.ts",
    "!tests/**/*.spec.ts",
    "!tests/**/index.ts",
  ],
  coverageThreshold: {
    global: {
      branches: 80,
      functions: 80,
      lines: 85,
      statements: 85,
    },
  },
  coverageDirectory: "coverage",
  moduleFileExtensions: ["ts", "js", "json"],
  transform: {
    "^.+\\.ts$": [
      "ts-jest",
      {
        tsconfig: {
          target: "ES2022",
          module: "commonjs",
          esModuleInterop: true,
          skipLibCheck: true,
        },
      },
    ],
  },
  setupFilesAfterEnv: ["<rootDir>/setup.ts"],
  testTimeout: 10000,
  verbose: true,
};
