/**
 * Jest setup file for SmartScale tests
 * Runs before each test suite
 */

// Set test environment variables
process.env.AWS_REGION = "ap-south-1";
process.env.CLUSTER_ID = "smartscale-test";
process.env.NODE_ENV = "test";

// Suppress console output during tests (optional)
// global.console = {
//   ...console,
//   log: jest.fn(),
//   debug: jest.fn(),
//   info: jest.fn(),
//   warn: jest.fn(),
//   error: jest.fn(),
// };

// Add custom matchers or global test utilities here
beforeAll(() => {
  console.log("🧪 Starting SmartScale test suite");
});

afterAll(() => {
  console.log("✅ Test suite completed");
});
